"""Modbus TCP monitoring and maintenance UI for GMS detector boxes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import socket
import stat
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk

import utils
from common import (
    SEGMENT_OFF,
    SEGMENT_ON,
    SEGMENTS,
    create_gradient_bar,
    create_segment_display,
)
from core_utils import (
    decode_error_register,
    ip_to_register_words,
    normalize_ipv4,
    register_value,
    registers_to_ipv4,
)
from gms_core import reconnect_delay, validate_tftp_ipv4
from log_viewer import LogViewer
from ui_config import UI_SCALE
from virtual_keyboard import VirtualKeyboard

try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ConnectionException, ModbusIOException
    from pymodbus.pdu import ExceptionResponse

    PYMODBUS_AVAILABLE = True
except Exception:  # pragma: no cover - target dependency
    ModbusTcpClient = None
    ConnectionException = OSError
    ModbusIOException = OSError
    ExceptionResponse = ()
    PYMODBUS_AVAILABLE = False

SCALE_FACTOR = UI_SCALE
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TFTP_IP = ""
TFTP_ROOT_DIR = Path("/srv/tftp")
TFTP_DEVICE_SUBDIR = Path("GDS") / "ASGD-3200"
TFTP_DEVICE_FILENAME = "asgd3200.bin"
TFTP_DEVICE_TARGETS = (
    (TFTP_DEVICE_SUBDIR, TFTP_DEVICE_FILENAME),
    (Path("GDS") / "ASGD-3210", "asgd3210.bin"),
)
FIRMWARE_MIN_BYTES = 1024
FIRMWARE_MAX_BYTES = 32 * 1024 * 1024
FIRMWARE_TIMEOUT_SEC = 15 * 60
UI_TICK_MS = 50
UI_EVENT_BUDGET = 50
UI_TIME_BUDGET_SEC = 0.012


class _FirmwareCoordinator:
    """Process-wide lease for the shared TFTP firmware paths."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.claims: dict[tuple[object, int], tuple[str, float]] = {}


_GLOBAL_FIRMWARE_COORDINATOR = _FirmwareCoordinator()
_GLOBAL_FIRMWARE_STAGE_LOCK = threading.Lock()
LOGGER = logging.getLogger(__name__)


def sx(value: float) -> int:
    return int(value * SCALE_FACTOR)


def sy(value: float) -> int:
    return int(value * SCALE_FACTOR)


def get_local_ip(detector_ip: str | None = None) -> str:
    """Return the local source address used to reach one detector.

    Route selection must be based on the detector network, not an Internet
    route.  An empty string is safer than advertising loopback to a remote
    detector when no usable route exists.
    """
    if not detector_ip:
        return DEFAULT_TFTP_IP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        detector_ip = normalize_ipv4(detector_ip)
        sock.connect((detector_ip, 502))
        return validate_tftp_ipv4(sock.getsockname()[0])
    except (AttributeError, OSError, TypeError, ValueError):
        return DEFAULT_TFTP_IP
    finally:
        sock.close()


class ModbusUI:
    SETTINGS_FILE = utils.STATE_DIR / "modbus_settings.json"
    LEGACY_SETTINGS_FILE = BASE_DIR / "modbus_settings.json"
    GAS_FULL_SCALE = {"ORG": 9999, "ARF-T": 5000, "HMDS": 3000, "HC-100": 5000}
    GAS_TYPE_POSITIONS = {
        "ORG": (sx(115), sy(100)),
        "ARF-T": (sx(107), sy(100)),
        "HMDS": (sx(110), sy(100)),
        "HC-100": (sx(104), sy(100)),
    }
    LAMP_COLORS_ON = ["red", "red", "green", "yellow"]
    LAMP_COLORS_OFF = ["#fdc8c8", "#fdc8c8", "#e0fbba", "#fcf1bf"]
    MODEL_VALUE_TO_NAME = {0: "ASGD3200", 1: "ASGD3210"}
    LOG_MAX_ENTRIES = 1000
    MODEL_SELECT_REG = 40094
    SENSOR_MODEL_REG = 40030
    SENSOR_MODEL_REG_COUNT = 4
    SENSOR_MODEL_POLL_SEC = 2.0
    COMMUNICATION_INTERVAL = 0.2
    MAX_RECONNECT_ATTEMPTS = 5  # Compatibility constant; reconnect is now indefinite.

    @staticmethod
    def reg_addr(register: int) -> int:
        return int(register) - 40001

    def __init__(
        self,
        parent,
        num_boxes: int,
        gas_types: dict,
        alarm_callback,
        authorize_callback=None,
    ):
        self.parent = parent
        self.num_boxes = max(0, int(num_boxes))
        self.alarm_callback = alarm_callback
        self.authorize_callback = authorize_callback
        self.virtual_keyboard = VirtualKeyboard(parent)
        self.virtual_keyboard.set_num_boxes(self.num_boxes)

        self.ip_vars = [tk.StringVar() for _ in range(self.num_boxes)]
        self.tftp_ip_vars = [
            tk.StringVar(value=DEFAULT_TFTP_IP) for _ in range(self.num_boxes)
        ]
        self.fw_file_paths: list[str | None] = [None] * self.num_boxes
        self.fw_file_info: list[dict | None] = [None] * self.num_boxes
        self.entries: list[tk.Entry] = []
        self.action_buttons: list[tk.Button] = []
        self.box_frames: list[tk.Frame] = []
        self.box_data: list[tuple[tk.Canvas, list[int], tk.Canvas, int]] = []
        self.box_states: list[dict] = []
        self.settings_popups: list[tk.Toplevel | None] = [None] * self.num_boxes
        self.log_viewers: list[LogViewer | None] = [None] * self.num_boxes
        self.box_logs: list[list[tuple]] = [[] for _ in range(self.num_boxes)]
        self._log_sequence = [0] * self.num_boxes
        self._last_viewed_log_sequence = [0] * self.num_boxes
        # Kept for older callers; unread accounting no longer depends on length.
        self.last_viewed_log_len = [0] * self.num_boxes
        self.disconnection_counts = [0] * self.num_boxes
        self.disconnection_labels: list[tk.Label | None] = [None] * self.num_boxes
        self.reconnect_attempt_labels: list[tk.Label | None] = [None] * self.num_boxes

        # Per-box communication objects avoid collisions when two boxes share an IP.
        self.clients: dict[int, object] = {}
        self.connected_clients: dict[int, threading.Thread] = {}
        self.stop_flags: dict[int, threading.Event] = {}
        self.modbus_locks: dict[int, threading.Lock] = {}
        self._objects_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._shutdown_started = False
        self._connection_generation = [0] * self.num_boxes
        self._sample_accepting = [False] * self.num_boxes
        # Control events are never discarded. High-rate samples are coalesced
        # per box, while safety-state transitions are promoted to control events.
        self.ui_queue: queue.Queue[tuple[str, int, object, int | None]] = queue.Queue()
        self._sample_lock = threading.Lock()
        self._pending_samples: dict[tuple[int, int], object] = {}
        self._last_enqueued_safety: dict[tuple[int, int], tuple] = {}
        self._ui_after_id: str | None = None
        self._blink_after_id: str | None = None
        self._after_ids: set[str] = set()
        self._after_lock = threading.Lock()
        self._blink_phase = False

        self._worker_lock = threading.Lock()
        self._worker_threads: set[threading.Thread] = set()
        self._command_locks = [threading.Lock() for _ in range(self.num_boxes)]

        self._firmware_stage_lock = _GLOBAL_FIRMWARE_STAGE_LOCK
        self._firmware_owner = object()
        self._firmware_coordinator_lock = _GLOBAL_FIRMWARE_COORDINATOR.lock
        self._active_fw_digest: str | None = None
        self._active_fw_indices: set[int] = set()
        self._fw_deadlines: dict[int, tuple[str, float]] = {}
        self._staged_fw_digest: str | None = None

        self.extended_supported = [False] * self.num_boxes
        self.tftp_supported = [False] * self.num_boxes
        self.fw_status_supported = [False] * self.num_boxes
        self.sensor_model_supported = [False] * self.num_boxes
        self.last_fw_status: list[tuple | None] = [None] * self.num_boxes

        self.gradient_bar = create_gradient_bar(sx(120), sy(5))
        self._gradient_width = self.gradient_bar.width
        self.load_ip_settings()
        self._load_images()

        for index in range(self.num_boxes):
            self.create_modbus_box(index, gas_types)

        self._ui_after_id = self._schedule_after(UI_TICK_MS, self._process_ui_queue)
        self._blink_after_id = self._schedule_after(500, self._blink_tick)
        self._schedule_after(0, self._connect_saved_devices)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def _schedule_after(self, delay_ms: int, callback, *args, **kwargs):
        """Schedule one UI callback and keep it cancellable during shutdown."""
        if self._stop_event.is_set():
            return None
        holder: dict[str, str] = {}

        def run() -> None:
            after_id = holder.get("id")
            if after_id is not None:
                with self._after_lock:
                    self._after_ids.discard(after_id)
            if self._stop_event.is_set():
                return
            try:
                callback(*args, **kwargs)
            except tk.TclError:
                return
            except Exception as exc:
                LOGGER.exception("Scheduled UI callback failed: %s", exc)

        try:
            after_id = self.parent.after(max(0, int(delay_ms)), run)
        except tk.TclError:
            return None
        holder["id"] = after_id
        with self._after_lock:
            self._after_ids.add(after_id)
        return after_id

    def _cancel_after(self, after_id) -> None:
        if not after_id:
            return
        with self._after_lock:
            self._after_ids.discard(after_id)
        try:
            self.parent.after_cancel(after_id)
        except tk.TclError:
            pass

    def _start_worker(self, target, *args, name: str) -> threading.Thread | None:
        """Start and track a non-Tk worker unless shutdown has begun."""
        if self._stop_event.is_set():
            return None

        def run() -> None:
            try:
                target(*args)
            finally:
                current = threading.current_thread()
                with self._worker_lock:
                    self._worker_threads.discard(current)

        thread = threading.Thread(target=run, name=name, daemon=True)
        with self._worker_lock:
            if self._stop_event.is_set():
                return None
            self._worker_threads.add(thread)
        try:
            thread.start()
        except Exception:
            with self._worker_lock:
                self._worker_threads.discard(thread)
            raise
        return thread

    @staticmethod
    def _sample_safety_key(payload: object) -> tuple:
        if not isinstance(payload, dict):
            return ()
        try:
            fw = payload.get("fw")
            fw_status = None
            if isinstance(fw, (tuple, list)) and len(fw) >= 2:
                fw_status = int(fw[1]) & 0xFFFF
            return (
                bool(payload.get("alarm1")),
                bool(payload.get("alarm2")),
                int(payload.get("error_reg", 0)),
                fw_status,
            )
        except (TypeError, ValueError):
            # A malformed sample must not kill the producer thread. It is
            # promoted to the control queue so the UI handler can isolate it.
            return ("malformed", id(payload))

    def _load_images(self) -> None:
        self.connect_image = self._load_image(
            BASE_DIR / "img" / "on.png", (sx(50), sy(70)), "#3b8f3b"
        )
        self.disconnect_image = self._load_image(
            BASE_DIR / "img" / "off.png", (sx(50), sy(70)), "#9d3d3d"
        )

    def _load_image(self, path: Path, size: tuple[int, int], fallback: str):
        try:
            image = Image.open(path).convert("RGBA")
            resampling = getattr(Image, "Resampling", Image)
            image.thumbnail(size, resampling.LANCZOS)
        except Exception:
            image = Image.new("RGBA", size, fallback)
        return ImageTk.PhotoImage(image)

    def _put_ui(
        self,
        event_type: str,
        index: int,
        payload: object = None,
        generation: int | None = None,
    ) -> None:
        if self._stop_event.is_set() and event_type != "stopped":
            return
        if generation is None and 0 <= index < self.num_boxes:
            generation = self._connection_generation[index]
        if event_type == "sample":
            if generation is None or not 0 <= index < self.num_boxes:
                return
            if (
                generation != self._connection_generation[index]
                or not self._sample_accepting[index]
            ):
                return
            sample_key = (index, generation)
            safety_key = self._sample_safety_key(payload)
            with self._sample_lock:
                previous = self._last_enqueued_safety.get(sample_key)
                if safety_key != previous:
                    self._pending_samples.pop(sample_key, None)
                    self._last_enqueued_safety[sample_key] = safety_key
                    self.ui_queue.put((event_type, index, payload, generation))
                else:
                    self._pending_samples[sample_key] = payload
            return
        if (
            event_type
            in {
                "connecting",
                "connected",
                "connection_error",
                "disconnected",
                "failed",
                "stopped",
                "manual_disconnect",
            }
            and generation is not None
        ):
            # A lifecycle boundary invalidates any steady-state sample that has
            # not yet reached Tk. Safety transitions already in the FIFO stay
            # ordered, while stale coalesced telemetry is removed.
            with self._sample_lock:
                sample_key = (index, generation)
                self._pending_samples.pop(sample_key, None)
                self._last_enqueued_safety.pop(sample_key, None)
        self.ui_queue.put((event_type, index, payload, generation))

    def _ui_call(self, callback, *args, **kwargs) -> None:
        self._put_ui("callback", -1, (callback, args, kwargs))

    def _request_authorization(self, callback) -> None:
        """Run a UI action only after the host's central authorization gate."""
        if self._stop_event.is_set():
            return
        if self.authorize_callback is None:
            # Standalone/demo callers intentionally have no central PIN host.
            callback()
            return
        try:
            self.authorize_callback(lambda: self._ui_call(callback))
        except Exception as exc:
            self._show_error("권한 확인", f"관리자 권한 확인에 실패했습니다.\n{exc}")

    def _show_message_ui(self, kind: str, title: str, text: str) -> None:
        parent = self.parent.winfo_toplevel()
        functions = {
            "info": messagebox.showinfo,
            "warning": messagebox.showwarning,
            "error": messagebox.showerror,
        }
        functions[kind](title, text, parent=parent)

    def _show_info(self, title: str, text: str) -> None:
        self._ui_call(self._show_message_ui, "info", title, text)

    def _show_warning(self, title: str, text: str) -> None:
        self._ui_call(self._show_message_ui, "warning", title, text)

    def _show_error(self, title: str, text: str) -> None:
        self._ui_call(self._show_message_ui, "error", title, text)

    @staticmethod
    def _is_error_response(response) -> bool:
        if response is None:
            return True
        if ExceptionResponse and isinstance(response, ExceptionResponse):
            return True
        try:
            return bool(response.isError())
        except Exception:
            return True

    @staticmethod
    def _registers(response) -> list[int]:
        values = getattr(response, "registers", None)
        return [int(value) for value in values] if values else []

    @staticmethod
    def _write_ack_matches(response, address: int, values: list[int]) -> bool:
        """Validate echoed write metadata when PyModbus exposes it."""
        try:
            response_address = getattr(response, "address", address)
            if int(response_address) != int(address):
                return False
            response_count = getattr(response, "count", len(values))
            if len(values) > 1 and int(response_count) != len(values):
                return False
            echoed = getattr(response, "registers", None)
            if echoed:
                echoed_values = [int(value) & 0xFFFF for value in echoed]
                expected = [int(value) & 0xFFFF for value in values]
                if echoed_values[: len(expected)] != expected:
                    return False
            elif len(values) == 1 and hasattr(response, "value"):
                if int(response.value) & 0xFFFF != int(values[0]) & 0xFFFF:
                    return False
        except (AttributeError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def regs_to_ascii(registers: list[int]) -> str:
        data = bytearray()
        for word in registers:
            data.extend(((int(word) >> 8) & 0xFF, int(word) & 0xFF))
        return data.decode("ascii", errors="ignore").replace("\x00", "").strip()

    def _notify_alarm(self, active: bool, box_id: str, fut: bool = False) -> None:
        try:
            self.alarm_callback(active, box_id, fut)
        except TypeError:
            try:
                self.alarm_callback(active, box_id)
            except Exception as exc:
                LOGGER.exception("Alarm callback failed: %s", exc)
        except Exception as exc:
            LOGGER.exception("Alarm callback failed: %s", exc)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def load_ip_settings(self) -> None:
        path = self.SETTINGS_FILE
        migrated = False
        if not path.exists():
            legacy = self.LEGACY_SETTINGS_FILE
            if legacy != path and legacy.is_file() and not legacy.is_symlink():
                path = legacy
                migrated = True
            else:
                return
        if path.is_symlink() or not path.is_file():
            return
        try:
            if path.stat().st_size > 64 * 1024:
                return
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(values, list):
            return
        normalized: list[str] = []
        for index, value in enumerate(values[: self.num_boxes]):
            try:
                ip = normalize_ipv4(value)
            except (AttributeError, TypeError, ValueError):
                normalized.append("")
                continue
            normalized.append(ip)
            self.ip_vars[index].set(ip)
        if path == self.SETTINGS_FILE:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        elif migrated:
            # Preserve the legacy file for rollback, but atomically seed the
            # configured state directory with validated data.
            try:
                self._write_ip_settings(normalized)
            except OSError as exc:
                LOGGER.warning("Modbus IP settings migration failed: %s", exc)

    def _write_ip_settings(self, values: list[str]) -> None:
        directory = utils.ensure_state_dir()
        fd, temp_name = tempfile.mkstemp(
            prefix=".modbus_settings.", suffix=".tmp", dir=directory
        )
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            handle = os.fdopen(fd, "w", encoding="utf-8", closefd=True)
            fd = -1
            with handle:
                json.dump(values, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.SETTINGS_FILE)
            try:
                os.chmod(self.SETTINGS_FILE, 0o600)
            except OSError:
                pass
            utils.fsync_directory(directory)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def save_ip_settings(self) -> None:
        values = [variable.get().strip() for variable in self.ip_vars]
        try:
            normalized = []
            for value in values:
                try:
                    normalized.append(normalize_ipv4(value) if value else "")
                except ValueError:
                    normalized.append("")
            self._write_ip_settings(normalized)
        except OSError as exc:
            print(f"Modbus IP 설정 저장 오류: {exc}")

    def _connect_saved_devices(self) -> None:
        """Automatically start monitoring every persisted, valid detector IP."""
        if self._stop_event.is_set():
            return
        for index, variable in enumerate(self.ip_vars):
            try:
                ip = normalize_ipv4(variable.get())
            except ValueError:
                continue
            variable.set(ip)
            route_ip = get_local_ip(ip)
            self.tftp_ip_vars[index].set(route_ip)
            self.connect(index)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def create_modbus_box(self, index: int, gas_types: dict) -> None:
        frame = tk.Frame(
            self.parent,
            highlightthickness=3,
            highlightbackground="#000000",
            highlightcolor="#000000",
        )
        inner = tk.Frame(frame)
        inner.pack()
        canvas = tk.Canvas(
            inner,
            width=sx(150),
            height=sy(300),
            highlightthickness=1,
            highlightbackground="#000000",
            bg="#1e1e1e",
        )
        canvas.pack()
        canvas.create_rectangle(0, 0, sx(160), sy(200), fill="grey", outline="grey")
        canvas.create_rectangle(
            0, sy(200), sx(160), sy(310), fill="black", outline="black"
        )
        create_segment_display(canvas)

        gas_type = gas_types.get(f"modbus_box_{index}", "ORG")
        if gas_type not in self.GAS_FULL_SCALE:
            gas_type = "ORG"
        gas_var = tk.StringVar(value=gas_type)
        gas_text = canvas.create_text(
            *self.GAS_TYPE_POSITIONS[gas_type],
            text=gas_type,
            font=("Helvetica", sx(16), "bold"),
            fill="#cccccc",
        )
        version_text = canvas.create_text(
            sx(140),
            sy(12),
            text="",
            font=("Helvetica", sx(8), "bold"),
            fill="#cccccc",
            anchor="ne",
        )
        badge_bg = canvas.create_rectangle(
            sx(6),
            sy(6),
            sx(55),
            sy(20),
            fill="#2b2b2b",
            outline="#444444",
            state="hidden",
        )
        badge_text = canvas.create_text(
            sx(10),
            sy(8),
            text="LOG 0",
            font=("Helvetica", sx(8), "bold"),
            fill="#ffd966",
            anchor="nw",
            state="hidden",
        )

        circles = [
            canvas.create_oval(sx(57), sy(158), sx(67), sy(168)),
            canvas.create_oval(sx(93), sy(158), sx(103), sy(168)),
            canvas.create_oval(sx(20), sy(158), sx(30), sy(168)),
            canvas.create_oval(sx(131), sy(158), sx(141), sy(168)),
        ]
        for x, label in ((62, "AL1"), (98, "AL2"), (25, "PWR"), (136, "FUT")):
            canvas.create_text(
                sx(x), sy(182), text=label, fill="#cccccc", font=("Helvetica", sx(8))
            )

        control = tk.Frame(canvas, bg="black")
        control.place(x=sx(8), y=sy(207))
        entry = tk.Entry(
            control,
            textvariable=self.ip_vars[index],
            width=14,
            justify="center",
            bg="#2e2e2e",
            fg="white",
            insertbackground="white",
            font=("Helvetica", sx(9)),
        )
        entry.grid(row=0, column=0, padx=(0, 2), pady=3)
        entry.bind(
            "<Button-1>",
            lambda _event, widget=entry: self.virtual_keyboard.show(widget),
            add="+",
        )
        button = tk.Button(
            control,
            image=self.connect_image,
            command=lambda i=index: self.toggle_connection(i),
            width=sx(48),
            height=sy(34),
            bd=0,
            highlightthickness=0,
            bg="black",
            activebackground="black",
        )
        button.grid(row=0, column=1)
        dc_label = tk.Label(
            control, text="DC: 0", fg="white", bg="black", font=("Helvetica", sx(8))
        )
        dc_label.grid(row=1, column=0, columnspan=2)
        reconnect_label = tk.Label(
            control,
            text="Reconnect: idle",
            fg="yellow",
            bg="black",
            font=("Helvetica", sx(8)),
        )
        reconnect_label.grid(row=2, column=0, columnspan=2)
        dc_label.grid_remove()
        reconnect_label.grid_remove()

        gms_text = canvas.create_text(
            sx(80),
            sy(270),
            text="GMS-1000",
            font=("Helvetica", sx(16), "bold"),
            fill="#cccccc",
        )
        canvas.create_text(
            sx(80),
            sy(295),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica", sx(7), "bold"),
            fill="#cccccc",
        )

        bar_canvas = tk.Canvas(
            canvas,
            width=self._gradient_width,
            height=self.gradient_bar.height,
            bg="black",
            highlightthickness=0,
        )
        bar_canvas.place(x=sx(18), y=sy(75))
        bar_item = bar_canvas.create_image(0, 0, anchor="nw", state="hidden")

        click_area = canvas.create_rectangle(
            sx(10), sy(25), sx(140), sy(90), outline="", fill=""
        )
        canvas.tag_bind(
            click_area, "<Button-1>", lambda _event, i=index: self.open_log_viewer(i)
        )
        canvas.segment_canvas.bind(
            "<Button-1>", lambda _event, i=index: self.open_log_viewer(i)
        )
        for circle in circles:
            canvas.tag_bind(
                circle,
                "<Button-1>",
                lambda _event, i=index: self.open_settings_popup(i),
            )

        state = {
            "connected": False,
            "communication_fault": False,
            "healthy_sample_streak": 0,
            "manual_disconnect": False,
            "ip": "",
            "value": 0,
            "bar_value": 0,
            "bar_render_key": None,
            "alarm1": False,
            "alarm2": False,
            "error_reg": 0,
            "error_display": "",
            "version": None,
            "sensor_model": "",
            "gas_type_var": gas_var,
            "gas_type_text_id": gas_text,
            "version_text_id": version_text,
            "gms_text_id": gms_text,
            "log_badge_bg": badge_bg,
            "log_badge_text": badge_text,
            "fw_file_name_var": tk.StringVar(value="(파일 없음)"),
            "fw_status_var": tk.StringVar(value=""),
            "fw_upgrade_btn": None,
            "fw_cmd_inflight": False,
            "fw_upgrading": False,
            "fw_digest": None,
            "fw_started_at": None,
            "fw_seen_active": False,
            "fw_baseline_status": None,
            "fw_ambiguous": False,
            "fw_timeout_after_id": None,
            "fw_clear_after_id": None,
            "fw_campaign_sequence": 0,
            "last_log_value": None,
            "last_log_alarm1": None,
            "last_log_alarm2": None,
            "last_log_error": None,
        }
        self.entries.append(entry)
        self.action_buttons.append(button)
        self.disconnection_labels[index] = dc_label
        self.reconnect_attempt_labels[index] = reconnect_label
        self.box_frames.append(frame)
        self.box_data.append((canvas, circles, bar_canvas, bar_item))
        self.box_states.append(state)
        self._render_box(index)
        self.update_log_badge(index)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def toggle_connection(self, index: int) -> None:
        if self.box_states[index]["connected"] or index in self.connected_clients:
            generation = self._connection_generation[index]

            def authorized_disconnect() -> None:
                if (
                    self._stop_event.is_set()
                    or generation != self._connection_generation[index]
                ):
                    return
                if (
                    self.box_states[index]["connected"]
                    or index in self.connected_clients
                ):
                    self.disconnect(index, manual=True)

            self._request_authorization(authorized_disconnect)
        else:
            self.connect(index)

    def connect(self, index: int) -> None:
        if self._stop_event.is_set() or not 0 <= index < self.num_boxes:
            return
        with self._objects_lock:
            if index in self.connected_clients:
                return
        if not PYMODBUS_AVAILABLE:
            messagebox.showerror(
                "Modbus",
                "pymodbus가 설치되지 않았습니다. requirements.txt를 설치한 뒤 다시 시도하세요.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        try:
            ip = normalize_ipv4(self.ip_vars[index].get())
        except ValueError as exc:
            messagebox.showwarning(
                "IP 주소", str(exc), parent=self.parent.winfo_toplevel()
            )
            return
        self.ip_vars[index].set(ip)
        self.tftp_ip_vars[index].set(get_local_ip(ip))
        self.save_ip_settings()
        stop_flag = threading.Event()
        with self._objects_lock:
            if self._stop_event.is_set() or index in self.connected_clients:
                return
            self._connection_generation[index] += 1
            generation = self._connection_generation[index]
            # Build the final thread only after assigning this connection
            # generation so all of its events can be rejected if superseded.
            thread = threading.Thread(
                target=self._connection_worker,
                args=(index, ip, stop_flag, generation),
                name=f"gms-modbus-{index}",
                daemon=True,
            )
            self.stop_flags[index] = stop_flag
            self.connected_clients[index] = thread
            self.modbus_locks[index] = threading.Lock()
            self._sample_accepting[index] = True
        with self._sample_lock:
            for key in [key for key in self._pending_samples if key[0] == index]:
                self._pending_samples.pop(key, None)
            for key in [key for key in self._last_enqueued_safety if key[0] == index]:
                self._last_enqueued_safety.pop(key, None)
        self.box_states[index]["manual_disconnect"] = False
        self.box_states[index]["communication_fault"] = True
        self.box_states[index]["healthy_sample_streak"] = 0
        self.entries[index].config(state="disabled")
        self.reconnect_attempt_labels[index].config(text="Connecting: 1")
        self.reconnect_attempt_labels[index].grid()
        try:
            thread.start()
        except Exception:
            with self._objects_lock:
                if self.connected_clients.get(index) is thread:
                    self._sample_accepting[index] = False
                    self.connected_clients.pop(index, None)
                    self.stop_flags.pop(index, None)
                    self.modbus_locks.pop(index, None)
            self.entries[index].config(state="normal")
            self.action_buttons[index].config(image=self.connect_image)
            raise

    def _connection_worker(
        self,
        index: int,
        ip: str,
        stop_flag: threading.Event,
        generation: int | None = None,
    ) -> None:
        if generation is None:
            generation = self._connection_generation[index]
        failure_count = 0
        was_connected = False
        try:
            while not self._stop_event.is_set() and not stop_flag.is_set():
                client = None
                failure_count += 1
                self._put_ui("connecting", index, failure_count, generation)
                try:
                    # PyModbus otherwise retransmits every request three times,
                    # including non-idempotent maintenance writes.
                    client = ModbusTcpClient(ip, port=502, timeout=3, retries=0)
                    if not client.connect():
                        raise ConnectionException(f"{ip}:502 연결 실패")
                    if stop_flag.is_set() or self._stop_event.is_set():
                        break
                    with self._objects_lock:
                        self.clients[index] = client
                        lock = self.modbus_locks.get(index)
                    if lock is None:
                        raise RuntimeError("Modbus connection lock is unavailable")
                    with lock:
                        if stop_flag.is_set() or self._stop_event.is_set():
                            break
                        capabilities = self._probe_capabilities(client, index)
                    if stop_flag.is_set() or self._stop_event.is_set():
                        break
                    self._put_ui("connected", index, capabilities, generation)
                    was_connected = True
                    healthy_samples = 0
                    last_sensor_poll = 0.0
                    while not self._stop_event.is_set() and not stop_flag.is_set():
                        sample, last_sensor_poll = self._read_sample(
                            client, index, last_sensor_poll
                        )
                        self._put_ui("sample", index, sample, generation)
                        healthy_samples += 1
                        if healthy_samples >= 2:
                            # A TCP handshake alone is not a stable recovery.
                            # Keep increasing backoff for connections that flap
                            # before two complete detector samples arrive.
                            failure_count = 0
                        if stop_flag.wait(self.COMMUNICATION_INTERVAL):
                            break
                except Exception as exc:
                    if not stop_flag.is_set() and not self._stop_event.is_set():
                        self._put_ui(
                            "connection_error",
                            index,
                            {
                                "error": str(exc),
                                "attempt": max(1, failure_count),
                                "was_connected": was_connected,
                            },
                            generation,
                        )
                finally:
                    close_lock = None
                    with self._objects_lock:
                        if self.clients.get(index) is client:
                            self.clients.pop(index, None)
                            close_lock = self.modbus_locks.get(index)
                    if client is not None:
                        try:
                            if close_lock is None:
                                client.close()
                            else:
                                with close_lock:
                                    client.close()
                        except Exception:
                            pass

                if stop_flag.is_set() or self._stop_event.is_set():
                    break
                if was_connected:
                    self._put_ui("disconnected", index, None, generation)
                    was_connected = False
                retry_attempt = max(1, failure_count)
                delay = reconnect_delay(retry_attempt)
                self._put_ui("retry_wait", index, (retry_attempt, delay), generation)
                if stop_flag.wait(delay):
                    break
        finally:
            current_thread = threading.current_thread()
            owns_slot = False
            with self._objects_lock:
                if self.connected_clients.get(index) is current_thread:
                    owns_slot = True
                    self._sample_accepting[index] = False
                    self.clients.pop(index, None)
                    self.connected_clients.pop(index, None)
                    self.modbus_locks.pop(index, None)
                    self.stop_flags.pop(index, None)
            if owns_slot:
                self._put_ui(
                    "stopped",
                    index,
                    {"manual": stop_flag.is_set()},
                    generation,
                )

    def _probe_capabilities(self, client, index: int) -> dict:
        base_response = client.read_holding_registers(
            address=self.reg_addr(40001), count=22
        )
        if (
            self._is_error_response(base_response)
            or len(self._registers(base_response)) < 22
        ):
            raise ModbusIOException(f"기본 레지스터 읽기 실패: {base_response}")

        extended = False
        try:
            response = client.read_holding_registers(
                address=self.reg_addr(40001), count=24
            )
            extended = (
                not self._is_error_response(response)
                and len(self._registers(response)) >= 24
            )
        except Exception:
            extended = False

        sensor_model = ""
        sensor_supported = False
        try:
            response = client.read_holding_registers(
                address=self.reg_addr(self.SENSOR_MODEL_REG),
                count=self.SENSOR_MODEL_REG_COUNT,
            )
            registers = self._registers(response)
            if (
                not self._is_error_response(response)
                and len(registers) == self.SENSOR_MODEL_REG_COUNT
            ):
                sensor_model = self.regs_to_ascii(registers)
                sensor_supported = True
        except Exception:
            pass

        tftp_ip = None
        tftp_supported = False
        if extended:
            try:
                response = client.read_holding_registers(
                    address=self.reg_addr(40088), count=2
                )
                registers = self._registers(response)
                if not self._is_error_response(response) and len(registers) == 2:
                    tftp_supported = True
                    try:
                        tftp_ip = validate_tftp_ipv4(registers_to_ipv4(registers))
                    except ValueError:
                        # The registers exist, but an invalid/stale value must
                        # never replace the route-derived local server address.
                        tftp_ip = None
            except Exception:
                pass
        return {
            "extended": extended,
            "fw_status": extended,
            "tftp": tftp_supported,
            "sensor_supported": sensor_supported,
            "sensor_model": sensor_model,
            "tftp_ip": tftp_ip,
        }

    def _read_sample(
        self, client, index: int, last_sensor_poll: float
    ) -> tuple[dict, float]:
        count = 24 if self.extended_supported[index] else 22
        with self._objects_lock:
            lock = self.modbus_locks.setdefault(index, threading.Lock())
        with lock:
            response = client.read_holding_registers(
                address=self.reg_addr(40001), count=count
            )
        raw_regs = self._registers(response)
        if self._is_error_response(response):
            raise ModbusIOException(f"레지스터 읽기 실패: {response}")
        if len(raw_regs) < count:
            raise ModbusIOException(f"레지스터 개수 부족: {len(raw_regs)}/{count}")

        error_reg = register_value(raw_regs, 40007)  # 40007 is index 6.
        value_40001 = register_value(raw_regs, 40001)
        sampled_at = time.monotonic()
        sample = {
            "value": register_value(raw_regs, 40005),
            "error_reg": error_reg,
            "error_display": decode_error_register(error_reg),
            "alarm1": bool(value_40001 & (1 << 6)),
            "alarm2": bool(value_40001 & (1 << 7)),
            "bar": register_value(raw_regs, 40011),
            "version": register_value(raw_regs, 40022),
            "fw": None,
            "sensor_model": None,
            "sample_monotonic": sampled_at,
        }
        if len(raw_regs) >= 24 and self.fw_status_supported[index]:
            sample["fw"] = (
                register_value(raw_regs, 40022),
                register_value(raw_regs, 40023),
                register_value(raw_regs, 40024),
            )

        now = time.monotonic()
        if (
            self.sensor_model_supported[index]
            and now - last_sensor_poll >= self.SENSOR_MODEL_POLL_SEC
        ):
            last_sensor_poll = now
            with lock:
                model_response = client.read_holding_registers(
                    address=self.reg_addr(self.SENSOR_MODEL_REG),
                    count=self.SENSOR_MODEL_REG_COUNT,
                )
            model_registers = self._registers(model_response)
            if (
                self._is_error_response(model_response)
                or len(model_registers) != self.SENSOR_MODEL_REG_COUNT
            ):
                raise ModbusIOException(
                    f"센서 모델 레지스터 읽기 실패: {model_response}"
                )
            sample["sensor_model"] = self.regs_to_ascii(model_registers)
        return sample, last_sensor_poll

    def disconnect(self, index: int, manual: bool = False) -> None:
        if not 0 <= index < self.num_boxes:
            return
        with self._objects_lock:
            flag = self.stop_flags.get(index)
            if manual:
                # Invalidate queued samples and maintenance workers as soon as
                # the operator authorizes a monitoring outage. The connection
                # worker still owns and closes its client safely.
                self._connection_generation[index] += 1
            generation = self._connection_generation[index]
            self._sample_accepting[index] = False
        if flag:
            flag.set()
        if manual:
            self._put_ui("manual_disconnect", index, None, generation)

    def _resolve_box_index(self, value, explicit_index=None) -> int | None:
        candidate = explicit_index if explicit_index is not None else value
        try:
            index = int(candidate)
        except (TypeError, ValueError):
            try:
                ip = normalize_ipv4(value)
            except ValueError:
                return None
            matches = [
                index
                for index, state in enumerate(self.box_states)
                if state.get("ip") == ip
            ]
            return matches[0] if len(matches) == 1 else None
        return index if 0 <= index < self.num_boxes else None

    def disconnect_client(
        self,
        ip_or_index,
        index: int | None = None,
        manual: bool = False,
        **kwargs,
    ) -> None:
        # Older integrations used ``i=`` for the box position.
        if index is None:
            index = kwargs.pop("i", None)
        target = self._resolve_box_index(ip_or_index, index)
        if target is not None:
            self.disconnect(target, manual=manual)

    def cleanup_client(self, ip_or_index) -> None:
        index = self._resolve_box_index(ip_or_index)
        if index is None:
            return
        with self._objects_lock:
            thread = self.connected_clients.get(index)
            if thread is not None and thread.is_alive():
                # The owning connection worker performs identity-safe cleanup.
                return
            self.clients.pop(index, None)
            self.connected_clients.pop(index, None)
            self.stop_flags.pop(index, None)
            self.modbus_locks.pop(index, None)

    def connect_to_server(self, ip: str, client) -> bool:
        attempt = 1
        while not self._stop_event.is_set():
            try:
                if client.connect():
                    return True
            except Exception:
                pass
            if self._stop_event.wait(reconnect_delay(attempt)):
                break
            attempt += 1
        return False

    # ------------------------------------------------------------------
    # UI queue handling
    # ------------------------------------------------------------------
    def _process_ui_queue(self) -> None:
        self._ui_after_id = None
        if self._stop_event.is_set():
            return
        started = time.monotonic()
        processed = 0
        while (
            processed < UI_EVENT_BUDGET
            and time.monotonic() - started < UI_TIME_BUDGET_SEC
        ):
            try:
                item = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            event_type, index = "invalid", -1
            try:
                if len(item) == 3:  # Compatibility with direct legacy queue users.
                    event_type, index, payload = item
                    generation = None
                elif len(item) == 4:
                    event_type, index, payload, generation = item
                else:
                    raise ValueError(f"invalid UI event tuple length: {len(item)}")
                self._handle_ui_event(event_type, index, payload, generation)
            except Exception as exc:
                LOGGER.exception(
                    "UI event failed (%s, box=%s): %s", event_type, index, exc
                )
            processed += 1

        # Coalesced samples are bounded by box count. Always process at least
        # one so a steady stream of control messages cannot starve readings.
        sample_count = 0
        while sample_count == 0 or time.monotonic() - started < UI_TIME_BUDGET_SEC:
            with self._sample_lock:
                if not self._pending_samples:
                    break
                (index, generation), payload = self._pending_samples.popitem()
            try:
                self._handle_ui_event("sample", index, payload, generation)
            except Exception as exc:
                LOGGER.exception("UI sample failed (box=%s): %s", index, exc)
            sample_count += 1
            if sample_count >= self.num_boxes:
                break

        with self._sample_lock:
            samples_pending = bool(self._pending_samples)
        delay = 1 if samples_pending or not self.ui_queue.empty() else UI_TICK_MS
        self._ui_after_id = self._schedule_after(delay, self._process_ui_queue)

    def _handle_ui_event(
        self,
        event_type: str,
        index: int,
        payload: object,
        generation: int | None = None,
    ) -> None:
        if event_type == "callback":
            callback, args, kwargs = payload
            callback(*args, **kwargs)
            return
        if not 0 <= index < self.num_boxes:
            return
        if generation is not None and generation != self._connection_generation[index]:
            return
        state = self.box_states[index]
        if event_type == "connecting":
            attempt = int(payload)
            state["manual_disconnect"] = False
            state["communication_fault"] = True
            state["healthy_sample_streak"] = 0
            label = self.reconnect_attempt_labels[index]
            if label:
                label.config(text=f"Reconnect: {attempt}")
                label.grid()
            self.entries[index].config(state="disabled")
            self.action_buttons[index].config(image=self.disconnect_image)
            self._render_box(index)
        elif event_type == "connected":
            capabilities = dict(payload) if isinstance(payload, dict) else {}
            state["connected"] = True
            state["manual_disconnect"] = False
            state["communication_fault"] = True
            state["healthy_sample_streak"] = 0
            state["ip"] = self.ip_vars[index].get()
            self.extended_supported[index] = bool(capabilities.get("extended"))
            self.fw_status_supported[index] = bool(capabilities.get("fw_status"))
            self.tftp_supported[index] = bool(capabilities.get("tftp"))
            self.sensor_model_supported[index] = bool(
                capabilities.get("sensor_supported")
            )
            state["sensor_model"] = str(capabilities.get("sensor_model") or "")
            route_ip = get_local_ip(state["ip"])
            self.tftp_ip_vars[index].set(route_ip)
            self.action_buttons[index].config(image=self.disconnect_image)
            self.entries[index].config(state="disabled")
            self.disconnection_labels[index].grid()
            self.reconnect_attempt_labels[index].config(text="Reconnect: OK")
            self.reconnect_attempt_labels[index].grid()
            self.box_data[index][0].itemconfig(state["gms_text_id"], state="hidden")
            self.show_bar(index, True)
            self._render_box(index)
        elif event_type == "sample" and isinstance(payload, dict):
            state["connected"] = True
            state["healthy_sample_streak"] = min(
                2, int(state.get("healthy_sample_streak", 0)) + 1
            )
            if state["healthy_sample_streak"] >= 2:
                state["communication_fault"] = False
            state["value"] = int(payload.get("value", 0))
            state["bar_value"] = int(payload.get("bar", 0))
            state["alarm1"] = bool(payload.get("alarm1"))
            state["alarm2"] = bool(payload.get("alarm2"))
            if state["alarm2"]:
                state["alarm1"] = True
            state["error_reg"] = int(payload.get("error_reg", 0))
            state["error_display"] = str(payload.get("error_display") or "")
            state["version"] = payload.get("version")
            if payload.get("sensor_model"):
                state["sensor_model"] = str(payload["sensor_model"])
            self._render_box(index)
            self.maybe_log_event(
                index,
                state["value"],
                state["alarm1"],
                state["alarm2"],
                state["error_reg"],
            )
            if payload.get("fw"):
                version, status, progress = payload["fw"]
                self.update_fw_status(
                    index,
                    version,
                    status,
                    progress,
                    sample_monotonic=payload.get("sample_monotonic"),
                )
        elif event_type in ("disconnected", "connection_error"):
            if state["connected"]:
                self.disconnection_counts[index] += 1
            state["connected"] = False
            state["communication_fault"] = True
            state["healthy_sample_streak"] = 0
            label = self.disconnection_labels[index]
            if label:
                label.config(text=f"DC: {self.disconnection_counts[index]}")
                label.grid()
            self._reset_display_state(
                index, communication_fault=True, preserve_alarm=True
            )
            if event_type == "connection_error" and isinstance(payload, dict):
                retry_label = self.reconnect_attempt_labels[index]
                if retry_label:
                    retry_label.config(
                        text=f"Fault; retry {int(payload.get('attempt', 1))}"
                    )
                    retry_label.grid()
        elif event_type == "retry_wait" and isinstance(payload, tuple):
            attempt, delay = payload
            label = self.reconnect_attempt_labels[index]
            if label:
                label.config(text=f"Retry {int(attempt)} in {float(delay):.0f}s")
                label.grid()
        elif event_type == "failed":
            state["connected"] = False
            state["communication_fault"] = True
            state["healthy_sample_streak"] = 0
            self.reconnect_attempt_labels[index].config(text="Reconnect: Failed")
            self._reset_display_state(
                index, communication_fault=True, preserve_alarm=True
            )
        elif event_type in ("stopped", "manual_disconnect"):
            if event_type == "manual_disconnect":
                state["manual_disconnect"] = True
                state["connected"] = False
                # An intentional monitoring outage is still a loss of the
                # safety channel. Keep FUT active and retain the last observed
                # gas alarm until fresh samples prove the detector state.
                state["communication_fault"] = True
                state["healthy_sample_streak"] = 0
                self.entries[index].config(state="normal")
                self.action_buttons[index].config(image=self.connect_image)
                self.disconnection_labels[index].grid_remove()
                self.reconnect_attempt_labels[index].grid_remove()
                self.box_data[index][0].itemconfig(state["gms_text_id"], state="normal")
                self._reset_display_state(
                    index, communication_fault=True, preserve_alarm=True
                )
            elif not state.get("manual_disconnect") and not self._stop_event.is_set():
                state["connected"] = False
                state["communication_fault"] = True
                self.entries[index].config(state="normal")
                self.action_buttons[index].config(image=self.connect_image)
                retry_label = self.reconnect_attempt_labels[index]
                if retry_label:
                    retry_label.config(text="Monitor stopped; reconnect manually")
                    retry_label.grid()
                self._reset_display_state(
                    index, communication_fault=True, preserve_alarm=True
                )
        elif event_type == "fw_message" and isinstance(payload, tuple):
            inflight, text = payload
            self._set_fw_ui(index, bool(inflight), str(text))
        elif event_type == "fw_command_sent" and isinstance(payload, dict):
            digest = str(payload.get("digest") or "")
            if not digest or state.get("fw_digest") != digest:
                return
            with self._firmware_coordinator_lock:
                claim = self._fw_deadlines.get(index)
            if claim is not None and claim[0] == digest:
                state["fw_started_at"] = float(
                    payload.get("sent_at") or time.monotonic()
                )
        elif event_type == "fw_started" and isinstance(payload, dict):
            digest = str(payload.get("digest") or "")
            if not digest or state.get("fw_digest") != digest:
                return
            with self._firmware_coordinator_lock:
                claim = self._fw_deadlines.get(index)
            if claim is None or claim[0] != digest:
                return
            if claim[1] <= time.monotonic():
                self._firmware_timeout(index, digest)
                return
            sent_at = float(payload.get("sent_at") or time.monotonic())
            state["fw_started_at"] = sent_at
            state["fw_upgrading"] = True
            state["fw_ambiguous"] = bool(payload.get("ambiguous"))
            self._set_fw_ui(index, True, str(payload.get("text") or "상태 확인 중…"))
            self._cancel_firmware_timeout(index)
            remaining = max(0.001, claim[1] - time.monotonic())
            state["fw_timeout_after_id"] = self._schedule_after(
                int(remaining * 1000),
                self._firmware_timeout,
                index,
                digest,
                claim[1],
            )
        elif event_type == "fw_failed" and isinstance(payload, dict):
            digest = str(payload.get("digest") or "")
            if digest and state.get("fw_digest") != digest:
                return
            state["fw_cmd_inflight"] = False
            state["fw_upgrading"] = False
            state["fw_ambiguous"] = False
            self._cancel_firmware_timeout(index)
            self._release_firmware(index, digest or state.get("fw_digest"))
            self._set_fw_ui(index, False, str(payload.get("text") or "FW 작업 실패"))
        elif event_type == "fw_command_done" and isinstance(payload, dict):
            digest = str(payload.get("digest") or "")
            if digest and state.get("fw_digest") != digest:
                return
            state["fw_cmd_inflight"] = False
            if state.get("fw_upgrading"):
                self._set_fw_ui(
                    index,
                    True,
                    str(state["fw_status_var"].get() or "업그레이드 상태 확인 중…"),
                )
        elif event_type == "tftp_ip" and isinstance(payload, str):
            try:
                self.tftp_ip_vars[index].set(validate_tftp_ipv4(payload))
            except ValueError:
                pass
        elif event_type == "capabilities" and isinstance(payload, dict):
            self.extended_supported[index] = bool(payload.get("extended"))
            self.fw_status_supported[index] = bool(payload.get("fw_status"))
            self.tftp_supported[index] = bool(payload.get("tftp"))
            self.sensor_model_supported[index] = bool(payload.get("sensor_supported"))
            if payload.get("sensor_model"):
                state["sensor_model"] = str(payload["sensor_model"])
                self._update_topright_label(index)
        elif event_type == "capability_disabled":
            self.extended_supported[index] = False
            self.fw_status_supported[index] = False
            self.tftp_supported[index] = False

    def _reset_display_state(
        self,
        index: int,
        *,
        communication_fault: bool = False,
        preserve_alarm: bool = False,
    ) -> None:
        state = self.box_states[index]
        alarm1 = bool(state.get("alarm1")) if preserve_alarm else False
        alarm2 = bool(state.get("alarm2")) if preserve_alarm else False
        state.update(
            {
                "value": 0,
                "bar_value": 0,
                "bar_render_key": None,
                "alarm1": alarm1,
                "alarm2": alarm2,
                "error_reg": 0,
                "error_display": "",
                "version": None,
                "sensor_model": "",
                "communication_fault": bool(communication_fault),
            }
        )
        self.show_bar(index, False)
        self._render_box(index)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _blink_tick(self) -> None:
        self._blink_after_id = None
        if self._stop_event.is_set():
            return
        self._blink_phase = not self._blink_phase
        for index in range(self.num_boxes):
            try:
                self._render_box(index)
            except Exception as exc:
                LOGGER.exception("Blink render failed (box=%s): %s", index, exc)
        self._blink_after_id = self._schedule_after(500, self._blink_tick)

    def _render_box(self, index: int) -> None:
        state = self.box_states[index]
        canvas, circles, _bar_canvas, _bar_item = self.box_data[index]
        connected = bool(state["connected"])
        device_fault = bool(state["error_display"])
        communication_fault = bool(state.get("communication_fault"))
        fault_active = device_fault or communication_fault
        # Alarm and fault are independent safety states. A detector fault or
        # link outage must never suppress an already observed gas alarm.
        alarm1 = bool(state["alarm1"])
        alarm2 = bool(state["alarm2"])

        display = state["error_display"] if device_fault else str(state["value"])
        if (
            not connected
            or communication_fault
            or (device_fault and not self._blink_phase)
        ):
            display = "    "
        self.update_segment_display(display, index)
        self.update_bar(state["bar_value"], index)

        al1_on = alarm2 or (alarm1 and self._blink_phase)
        al2_on = alarm2 and self._blink_phase
        pwr_on = connected
        fut_on = fault_active and self._blink_phase
        colors = [
            "red" if al1_on else self.LAMP_COLORS_OFF[0],
            "red" if al2_on else self.LAMP_COLORS_OFF[1],
            "green" if pwr_on else self.LAMP_COLORS_OFF[2],
            "yellow" if fut_on else self.LAMP_COLORS_OFF[3],
        ]
        for item, color in zip(circles, colors):
            canvas.itemconfig(item, fill=color, outline=color)
        if alarm1 or alarm2:
            border = "#ff0000" if self._blink_phase else "#000000"
        elif fault_active:
            border = "#ffff00" if self._blink_phase else "#000000"
        else:
            border = "#000000"
        self.box_frames[index].config(highlightbackground=border)
        self._update_topright_label(index)
        self._notify_alarm(alarm1 or alarm2, f"modbus_{index}", fault_active)

    def update_segment_display(
        self, value, box_index: int = 0, blink: bool = False
    ) -> None:
        canvas = self.box_data[box_index][0]
        text = str(value)
        text = text.rjust(4)[:4]
        hide = blink and self._blink_phase
        for position, digit in enumerate(text):
            pattern = SEGMENTS.get(" " if hide else digit, SEGMENTS[" "])
            for segment_index, enabled in enumerate(pattern[:7]):
                canvas.segment_canvas.itemconfig(
                    f"segment_{position}_{chr(97 + segment_index)}",
                    fill=SEGMENT_ON if enabled == "1" else SEGMENT_OFF,
                )
            canvas.segment_canvas.itemconfig(
                f"segment_{position}_dot", fill=SEGMENT_OFF, outline=SEGMENT_OFF
            )

    def update_bar(self, value, box_index: int) -> None:
        canvas = self.box_data[box_index][2]
        item = self.box_data[box_index][3]
        state = self.box_states[box_index]
        try:
            percentage = max(0.0, min(1.0, float(value) / 100.0))
        except (TypeError, ValueError):
            percentage = 0.0
        connected = bool(state["connected"])
        render_key = (round(percentage, 4), connected)
        if state.get("bar_render_key") == render_key:
            return
        state["bar_render_key"] = render_key
        if percentage <= 0.0 or not connected:
            canvas.itemconfig(item, state="hidden")
            return
        width = max(1, int(self._gradient_width * percentage))
        image = ImageTk.PhotoImage(
            self.gradient_bar.crop((0, 0, width, self.gradient_bar.height))
        )
        canvas.itemconfig(item, image=image, state="normal")
        canvas._bar_image = image  # type: ignore[attr-defined]

    def show_bar(self, box_index: int, show: bool) -> None:
        canvas = self.box_data[box_index][2]
        item = self.box_data[box_index][3]
        canvas.itemconfig(
            item,
            state="normal"
            if show and self.box_states[box_index]["bar_value"]
            else "hidden",
        )

    def update_circle_state(self, states, box_index: int = 0) -> None:
        state = self.box_states[box_index]
        values = list(states) + [False] * 4
        state["alarm1"] = bool(values[0])
        state["alarm2"] = bool(values[1])
        state["connected"] = bool(values[2])
        state["error_display"] = "FUT" if bool(values[3]) else ""
        self._render_box(box_index)

    def _update_topright_label(self, index: int) -> None:
        state = self.box_states[index]
        version = state["version"]
        version_text = self.format_version(version) if version is not None else ""
        model = str(state["sensor_model"] or "")
        text = (
            f"{version_text} / {model}"
            if version_text and model
            else version_text or model
        )
        self.box_data[index][0].itemconfig(state["version_text_id"], text=text)

    @staticmethod
    def format_version(version: int) -> str:
        try:
            value = int(version)
        except (TypeError, ValueError):
            return f"v{version}"
        return f"v{value // 100}.{value % 100:02d}"

    def set_version_label(self, box_index: int, version: int) -> None:
        self.box_states[box_index]["version"] = version
        self._update_topright_label(box_index)

    def set_sensor_model_label(self, box_index: int, model: str) -> None:
        self.box_states[box_index]["sensor_model"] = str(model).strip()
        self._update_topright_label(box_index)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------
    def maybe_log_event(
        self, index: int, value: int, alarm1: bool, alarm2: bool, error_reg: int
    ) -> None:
        state = self.box_states[index]
        current = (int(value), bool(alarm1), bool(alarm2), int(error_reg))
        previous = (
            state.get("last_log_value"),
            state.get("last_log_alarm1"),
            state.get("last_log_alarm2"),
            state.get("last_log_error"),
        )
        if current == previous:
            return
        (
            state["last_log_value"],
            state["last_log_alarm1"],
            state["last_log_alarm2"],
            state["last_log_error"],
        ) = current
        entry = (time.strftime("%Y-%m-%d %H:%M:%S"), *current)
        logs = self.box_logs[index]
        logs.append(entry)
        self._log_sequence[index] += 1
        if len(logs) > self.LOG_MAX_ENTRIES:
            del logs[: len(logs) - self.LOG_MAX_ENTRIES]
        viewer = self.log_viewers[index]
        if viewer is not None:
            try:
                if viewer.winfo_exists():
                    self._last_viewed_log_sequence[index] = self._log_sequence[index]
            except tk.TclError:
                pass
        self.update_log_badge(index)

    def update_log_badge(self, index: int) -> None:
        state = self.box_states[index]
        canvas = self.box_data[index][0]
        unread = max(
            0, self._log_sequence[index] - self._last_viewed_log_sequence[index]
        )
        bg, text = state["log_badge_bg"], state["log_badge_text"]
        if unread <= 0:
            canvas.itemconfig(bg, state="hidden")
            canvas.itemconfig(text, state="hidden")
            return
        canvas.itemconfig(text, text=f"LOG {unread}", state="normal")
        canvas.update_idletasks()
        bbox = canvas.bbox(text)
        if bbox:
            x1, y1, x2, y2 = bbox
            canvas.coords(bg, x1 - 6, y1 - 3, x2 + 6, y2 + 3)
        canvas.itemconfig(bg, state="normal")

    def open_log_viewer(self, index: int) -> None:
        existing = self.log_viewers[index]
        if existing is not None:
            try:
                if existing.winfo_exists():
                    self._last_viewed_log_sequence[index] = self._log_sequence[index]
                    self.last_viewed_log_len[index] = len(self.box_logs[index])
                    self.update_log_badge(index)
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        self._last_viewed_log_sequence[index] = self._log_sequence[index]
        self.last_viewed_log_len[index] = len(self.box_logs[index])
        self.update_log_badge(index)

        def clear() -> None:
            self.box_logs[index].clear()
            self._last_viewed_log_sequence[index] = self._log_sequence[index]
            self.last_viewed_log_len[index] = 0
            self.update_log_badge(index)

        def closed() -> None:
            self.log_viewers[index] = None

        self.log_viewers[index] = LogViewer(
            self.parent,
            box_index=index,
            ip=self.ip_vars[index].get().strip() or "미연결",
            get_logs_callable=lambda: self.box_logs[index],
            on_clear_callable=clear,
            on_close_callable=closed,
        )

    # ------------------------------------------------------------------
    # Firmware and maintenance commands
    # ------------------------------------------------------------------
    @staticmethod
    def _firmware_file_info(source: str) -> dict:
        path = Path(source)
        if path.suffix.lower() != ".bin":
            raise ValueError("펌웨어 파일 확장자는 .bin이어야 합니다.")
        if path.is_symlink():
            raise ValueError("심볼릭 링크 펌웨어 파일은 사용할 수 없습니다.")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("일반 파일만 펌웨어로 사용할 수 있습니다.")
            if not FIRMWARE_MIN_BYTES <= before.st_size <= FIRMWARE_MAX_BYTES:
                raise ValueError("펌웨어 크기는 1KiB 이상 32MiB 이하여야 합니다.")
            digest = hashlib.sha256()
            handle = os.fdopen(fd, "rb", closefd=True)
            fd = -1
            with handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
                after = os.fstat(handle.fileno())
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise RuntimeError("해시 계산 중 펌웨어 파일이 변경되었습니다.")
            return {
                "path": str(path.resolve(strict=True)),
                "name": path.name,
                "size": int(before.st_size),
                "digest": digest.hexdigest(),
            }
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _firmware_summary(info: dict) -> str:
        return (
            f"파일: {info['name']}\n"
            f"크기: {int(info['size']):,} bytes\n"
            f"SHA-256: {info['digest']}"
        )

    def _remember_firmware(self, index: int, path: str) -> dict:
        info = self._firmware_file_info(path)
        self.fw_file_paths[index] = str(info["path"])
        self.fw_file_info[index] = info
        self.box_states[index]["fw_file_name_var"].set(
            f"{info['name']} ({info['digest'][:12]})"
        )
        return info

    def select_fw_file(self, index: int) -> None:
        path = filedialog.askopenfilename(
            parent=self.parent.winfo_toplevel(),
            title="FW 파일 선택",
            filetypes=[("BIN files", "*.bin")],
        )
        if not path:
            return
        try:
            info = self._remember_firmware(index, path)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "FW 파일",
                f"펌웨어 파일을 사용할 수 없습니다.\n{exc}",
                parent=self.parent.winfo_toplevel(),
            )
            return
        messagebox.showinfo(
            "FW 파일 확인",
            self._firmware_summary(info),
            parent=self.parent.winfo_toplevel(),
        )

    def select_fw_file_all(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.parent.winfo_toplevel(),
            title="FW 파일 선택(전체 적용)",
            filetypes=[("BIN files", "*.bin")],
        )
        if not path:
            return
        try:
            info = self._firmware_file_info(path)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "FW 파일",
                f"펌웨어 파일을 사용할 수 없습니다.\n{exc}",
                parent=self.parent.winfo_toplevel(),
            )
            return
        for index in range(self.num_boxes):
            self.fw_file_paths[index] = str(info["path"])
            self.fw_file_info[index] = dict(info)
            self.box_states[index]["fw_file_name_var"].set(
                f"{info['name']} ({info['digest'][:12]})"
            )
        messagebox.showinfo(
            "FW 파일 확인",
            "선택한 파일을 전체 박스에 적용했습니다.\n\n"
            + self._firmware_summary(info),
            parent=self.parent.winfo_toplevel(),
        )

    def _expire_stale_firmware_claims(self) -> None:
        now = time.monotonic()
        with self._firmware_coordinator_lock:
            expired = [
                index
                for index, (_digest, deadline) in self._fw_deadlines.items()
                if deadline <= now
            ]
        for index in expired:
            self._firmware_timeout(index)

    def _claim_firmware(self, index: int, digest: str) -> bool:
        self._expire_stale_firmware_claims()
        with self._firmware_coordinator_lock:
            now = time.monotonic()
            global_claims = _GLOBAL_FIRMWARE_COORDINATOR.claims
            for key, (_claimed_digest, deadline) in list(global_claims.items()):
                if deadline <= now:
                    global_claims.pop(key, None)
            active_digests = {claimed[0] for claimed in global_claims.values()}
            if active_digests and active_digests != {digest}:
                return False
            deadline = now + FIRMWARE_TIMEOUT_SEC
            global_claims[(self._firmware_owner, index)] = (digest, deadline)
            self._active_fw_digest = digest
            self._active_fw_indices.add(index)
            self._fw_deadlines[index] = (digest, deadline)
            return True

    def _release_firmware(self, index: int, digest: str | None = None) -> None:
        with self._firmware_coordinator_lock:
            current = self._fw_deadlines.get(index)
            if digest is not None and current is not None and current[0] != digest:
                return
            self._fw_deadlines.pop(index, None)
            _GLOBAL_FIRMWARE_COORDINATOR.claims.pop((self._firmware_owner, index), None)
            self._active_fw_indices.discard(index)
            if not self._active_fw_indices:
                self._active_fw_digest = None

    def _cancel_firmware_timeout(self, index: int) -> None:
        after_id = self.box_states[index].get("fw_timeout_after_id")
        if after_id:
            self._cancel_after(after_id)
        self.box_states[index]["fw_timeout_after_id"] = None

    def _firmware_timeout(
        self,
        index: int,
        digest: str | None = None,
        expected_deadline: float | None = None,
    ) -> None:
        if not 0 <= index < self.num_boxes:
            return
        state = self.box_states[index]
        current_digest = state.get("fw_digest")
        if digest is not None and current_digest != digest:
            return
        with self._firmware_coordinator_lock:
            claim = self._fw_deadlines.get(index)
        if claim is None:
            return
        if expected_deadline is not None and claim[1] != expected_deadline:
            return
        state["fw_upgrading"] = False
        state["fw_cmd_inflight"] = False
        state["fw_ambiguous"] = False
        self._cancel_firmware_timeout(index)
        self._set_fw_ui(index, False, "업그레이드 상태 확인 시간 초과")
        self._release_firmware(index, current_digest)

    def _begin_firmware_upgrade(
        self,
        index: int,
        info: dict,
        *,
        ask_confirmation: bool,
    ) -> None:
        if self._stop_event.is_set() or not 0 <= index < self.num_boxes:
            return
        state = self.box_states[index]
        if not state["connected"]:
            messagebox.showwarning(
                "FW",
                "먼저 Modbus 연결을 해주세요.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        if not self.tftp_supported[index]:
            messagebox.showwarning(
                "FW",
                "이 장치는 FW/TFTP 기능을 지원하지 않습니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        if state["fw_cmd_inflight"] or state["fw_upgrading"]:
            messagebox.showwarning(
                "FW",
                "이 장치의 펌웨어 작업이 이미 진행 중입니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        try:
            current = self._firmware_file_info(str(info["path"]))
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "FW",
                f"펌웨어 파일 재검증에 실패했습니다.\n{exc}",
                parent=self.parent.winfo_toplevel(),
            )
            return
        if current["digest"] != info["digest"]:
            messagebox.showerror(
                "FW",
                "선택 후 펌웨어 파일 내용이 변경되었습니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        try:
            tftp_ip = validate_tftp_ipv4(self.tftp_ip_vars[index].get())
        except ValueError as exc:
            messagebox.showwarning(
                "TFTP IP", str(exc), parent=self.parent.winfo_toplevel()
            )
            return
        if ask_confirmation and not messagebox.askyesno(
            "FW 업그레이드 확인",
            self._firmware_summary(current)
            + f"\nTFTP: {tftp_ip}\n\n이 장치의 업그레이드를 시작할까요?",
            parent=self.parent.winfo_toplevel(),
        ):
            return
        digest = str(current["digest"])
        if not self._claim_firmware(index, digest):
            messagebox.showwarning(
                "FW",
                "다른 펌웨어 이미지의 업그레이드가 진행 중입니다. "
                "완료 또는 15분 제한시간 후 다시 시도하세요.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        command_lock = self._command_locks[index]
        if not command_lock.acquire(blocking=False):
            self._release_firmware(index, digest)
            messagebox.showwarning(
                "FW",
                "이 장치에 다른 명령을 전송 중입니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        state["fw_cmd_inflight"] = True
        state["fw_digest"] = digest
        state["fw_started_at"] = None
        state["fw_seen_active"] = False
        state["fw_baseline_status"] = self.last_fw_status[index]
        state["fw_ambiguous"] = False
        self._cancel_after(state.get("fw_clear_after_id"))
        state["fw_clear_after_id"] = None
        state["fw_campaign_sequence"] = int(state.get("fw_campaign_sequence", 0)) + 1
        generation = self._connection_generation[index]
        with self._firmware_coordinator_lock:
            claim = self._fw_deadlines.get(index)
        if claim is not None and claim[0] == digest:
            state["fw_timeout_after_id"] = self._schedule_after(
                max(1, int((claim[1] - time.monotonic()) * 1000)),
                self._firmware_timeout,
                index,
                digest,
                claim[1],
            )
        self._set_fw_ui(index, True, f"검증 완료 {digest[:12]} / 명령 전송 중…")
        try:
            thread = self._start_worker(
                self._firmware_worker,
                index,
                str(current["path"]),
                tftp_ip,
                digest,
                command_lock,
                generation,
                name=f"gms-fw-{index}",
            )
        except Exception as exc:
            command_lock.release()
            state["fw_cmd_inflight"] = False
            self._cancel_firmware_timeout(index)
            self._release_firmware(index, digest)
            self._set_fw_ui(index, False, f"작업 시작 실패: {exc}")
            return
        if thread is None:
            command_lock.release()
            state["fw_cmd_inflight"] = False
            self._cancel_firmware_timeout(index)
            self._release_firmware(index, digest)
            self._set_fw_ui(index, False, "종료 중이어서 작업을 시작하지 않았습니다.")

    def start_firmware_upgrade_all(
        self, only_connected: bool = True, delay_sec: float = 0.5
    ) -> None:
        targets = [
            index
            for index in range(self.num_boxes)
            if self.fw_file_paths[index]
            and (not only_connected or self.box_states[index]["connected"])
            and self.tftp_supported[index]
            and not self.box_states[index]["fw_cmd_inflight"]
            and not self.box_states[index]["fw_upgrading"]
        ]
        infos: dict[int, dict] = {}
        try:
            for index in targets:
                infos[index] = self._firmware_file_info(str(self.fw_file_paths[index]))
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror(
                "FW",
                f"펌웨어 파일 검증에 실패했습니다.\n{exc}",
                parent=self.parent.winfo_toplevel(),
            )
            return
        digests = {info["digest"] for info in infos.values()}
        if len(digests) > 1:
            messagebox.showwarning(
                "FW",
                "일괄 업데이트는 SHA-256이 같은 FW 파일만 사용할 수 있습니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        if not targets:
            messagebox.showinfo(
                "FW",
                "일괄 업데이트 대상이 없습니다.\n연결 상태, FW 파일 및 지원 여부를 확인하세요.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        if not messagebox.askyesno(
            "FW 일괄 업데이트",
            f"{len(targets)}개 장치에 FW 업그레이드 명령을 순차 전송합니다.\n\n"
            + self._firmware_summary(next(iter(infos.values())))
            + "\n\n진행할까요?",
            parent=self.parent.winfo_toplevel(),
        ):
            return
        for offset, index in enumerate(targets):
            self._schedule_after(
                int(max(0.0, delay_sec) * 1000 * offset),
                self._begin_firmware_upgrade,
                index,
                infos[index],
                ask_confirmation=False,
            )

    def start_firmware_upgrade(self, index: int) -> None:
        source = self.fw_file_paths[index]
        if not source:
            messagebox.showwarning(
                "FW",
                "FW 파일을 먼저 선택해주세요.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        try:
            info = self._firmware_file_info(source)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showwarning(
                "FW",
                f"FW 파일을 사용할 수 없습니다.\n{exc}",
                parent=self.parent.winfo_toplevel(),
            )
            return
        self._begin_firmware_upgrade(index, info, ask_confirmation=True)

    def _get_client_and_lock(self, index: int):
        with self._objects_lock:
            return self.clients.get(index), self.modbus_locks.get(index)

    def _destination_matches_digest(self, destination: Path, digest: str) -> bool:
        if not destination.exists() or destination.is_symlink():
            return False
        try:
            return self._firmware_file_info(str(destination))["digest"] == digest
        except (OSError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _prepare_tftp_directory(relative_dir: Path) -> Path:
        if relative_dir.is_absolute() or any(
            part in {"", ".", ".."} for part in relative_dir.parts
        ):
            raise RuntimeError(f"안전하지 않은 TFTP 상대 경로입니다: {relative_dir}")

        current = TFTP_ROOT_DIR
        for directory in (
            current,
            *(
                current / Path(*relative_dir.parts[:i])
                for i in range(1, len(relative_dir.parts) + 1)
            ),
        ):
            if directory.is_symlink():
                raise RuntimeError(
                    f"TFTP 경로에 심볼릭 링크를 사용할 수 없습니다: {directory}"
                )
            try:
                directory.mkdir(mode=0o755)
            except FileExistsError:
                pass
            if directory.is_symlink() or not directory.is_dir():
                raise RuntimeError(
                    f"TFTP 경로가 안전한 디렉터리가 아닙니다: {directory}"
                )
        return current / relative_dir

    def _stage_firmware(self, source: str, digest: str) -> Path:
        with self._firmware_stage_lock:
            current = self._firmware_file_info(source)
            if current["digest"] != digest:
                raise RuntimeError("복사 직전 펌웨어 SHA-256이 변경되었습니다.")

            destinations: list[Path] = []
            for relative_dir, filename in TFTP_DEVICE_TARGETS:
                destination_dir = self._prepare_tftp_directory(relative_dir)
                destination = destination_dir / filename
                destinations.append(destination)
                if self._destination_matches_digest(destination, digest):
                    destination_fd = os.open(
                        destination,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        os.fchmod(destination_fd, 0o644)
                        os.fsync(destination_fd)
                    finally:
                        os.close(destination_fd)
                    continue

                fd, temp_name = tempfile.mkstemp(
                    prefix=f".{destination.name}.",
                    suffix=".tmp",
                    dir=destination_dir,
                )
                temp_path = Path(temp_name)
                try:
                    os.fchmod(fd, 0o644)
                    copied_digest = hashlib.sha256()
                    flags = (
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    source_fd = os.open(source, flags)
                    try:
                        before = os.fstat(source_fd)
                        if not stat.S_ISREG(before.st_mode):
                            raise RuntimeError("펌웨어 소스가 일반 파일이 아닙니다.")
                        if (
                            not FIRMWARE_MIN_BYTES
                            <= before.st_size
                            <= FIRMWARE_MAX_BYTES
                        ):
                            raise RuntimeError(
                                "복사 직전 펌웨어 크기가 변경되었습니다."
                            )
                        source_handle = os.fdopen(source_fd, "rb", closefd=True)
                        source_fd = -1
                        with source_handle:
                            destination_handle = os.fdopen(fd, "wb", closefd=True)
                            fd = -1
                            with destination_handle:
                                while chunk := source_handle.read(1024 * 1024):
                                    destination_handle.write(chunk)
                                    copied_digest.update(chunk)
                                after = os.fstat(source_handle.fileno())
                                destination_handle.flush()
                                os.fsync(destination_handle.fileno())
                        if (
                            before.st_dev,
                            before.st_ino,
                            before.st_size,
                            before.st_mtime_ns,
                        ) != (
                            after.st_dev,
                            after.st_ino,
                            after.st_size,
                            after.st_mtime_ns,
                        ):
                            raise RuntimeError(
                                "staging 중 펌웨어 파일이 변경되었습니다."
                            )
                    finally:
                        if source_fd >= 0:
                            os.close(source_fd)
                    if copied_digest.hexdigest() != digest:
                        raise RuntimeError(
                            "staging 중 펌웨어 SHA-256이 변경되었습니다."
                        )

                    if destination_dir.is_symlink():
                        raise RuntimeError(
                            f"TFTP 경로가 staging 중 변경되었습니다: {destination_dir}"
                        )
                    os.replace(temp_path, destination)
                    directory_fd = os.open(
                        destination_dir,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    if fd >= 0:
                        os.close(fd)
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            self._staged_fw_digest = digest
            return destinations[0]

    def _firmware_worker(
        self,
        index: int,
        source: str,
        tftp_ip: str,
        digest: str | None = None,
        command_lock: threading.Lock | None = None,
        generation: int | None = None,
    ) -> None:
        digest = digest or self._firmware_file_info(source)["digest"]
        if generation is None:
            generation = self._connection_generation[index]
        stage = "staging"
        try:
            self._stage_firmware(source, digest)

            if self._stop_event.is_set():
                stage = "shutdown"
                raise RuntimeError("프로그램이 종료 중입니다.")
            if generation != self._connection_generation[index]:
                stage = "connection_changed"
                raise RuntimeError("연결이 변경되어 FW 명령을 전송하지 않았습니다.")
            with self._firmware_coordinator_lock:
                claim = self._fw_deadlines.get(index)
            if claim is None or claim[0] != digest or claim[1] <= time.monotonic():
                stage = "lease_expired"
                raise RuntimeError("FW 작업 15분 제한시간이 끝났습니다.")

            stage = "connection"
            client, lock = self._get_client_and_lock(index)
            if client is None or lock is None:
                raise RuntimeError("Modbus 연결이 해제되었습니다.")
            word1, word2 = ip_to_register_words(tftp_ip)
            # Keep the shared TFTP image stable from final verification until
            # the detector has acknowledged (or ambiguously lost) the start
            # request. A different digest may be claimed after timeout, but it
            # cannot overwrite either model path halfway through the command pair.
            with self._firmware_stage_lock:
                destinations = [
                    TFTP_ROOT_DIR / relative_dir / filename
                    for relative_dir, filename in TFTP_DEVICE_TARGETS
                ]
                if not all(
                    self._destination_matches_digest(destination, digest)
                    for destination in destinations
                ):
                    stage = "staging"
                    raise RuntimeError(
                        "staging 파일 SHA-256이 전송 직전 변경되었습니다."
                    )
                with lock:
                    if self._stop_event.is_set():
                        stage = "shutdown"
                        raise RuntimeError("프로그램이 종료 중입니다.")
                    with self._objects_lock:
                        if (
                            self.clients.get(index) is not client
                            or generation != self._connection_generation[index]
                        ):
                            raise RuntimeError("Modbus 연결이 변경되었습니다.")
                    with self._firmware_coordinator_lock:
                        claim = self._fw_deadlines.get(index)
                    if (
                        claim is None
                        or claim[0] != digest
                        or claim[1] <= time.monotonic()
                    ):
                        stage = "lease_expired"
                        raise RuntimeError("FW 작업 15분 제한시간이 끝났습니다.")
                    stage = "tftp_ip"
                    response = client.write_registers(
                        address=self.reg_addr(40088), values=[word1, word2]
                    )
                    if self._is_error_response(response) or not self._write_ack_matches(
                        response, self.reg_addr(40088), [word1, word2]
                    ):
                        raise RuntimeError(f"TFTP IP 쓰기 실패: {response}")
                    with self._firmware_coordinator_lock:
                        claim = self._fw_deadlines.get(index)
                    if (
                        claim is None
                        or claim[0] != digest
                        or claim[1] <= time.monotonic()
                    ):
                        stage = "lease_expired"
                        raise RuntimeError(
                            "TFTP IP는 설정했지만 FW 시작 전 15분 제한시간이 끝났습니다."
                        )
                    stage = "start"
                    sent_at = time.monotonic()
                    self._put_ui(
                        "fw_command_sent",
                        index,
                        {"digest": digest, "sent_at": sent_at},
                        generation,
                    )
                    response = client.write_register(
                        address=self.reg_addr(40091), value=1
                    )
                    if self._is_error_response(response) or not self._write_ack_matches(
                        response, self.reg_addr(40091), [1]
                    ):
                        if response is not None:
                            try:
                                if response.isError() or not self._write_ack_matches(
                                    response, self.reg_addr(40091), [1]
                                ):
                                    stage = "start_rejected"
                            except Exception:
                                pass
                        raise RuntimeError(f"FW 시작 명령 실패: {response}")
            self._put_ui(
                "fw_started",
                index,
                {
                    "digest": digest,
                    "sent_at": sent_at,
                    "text": "명령 응답 확인. 업그레이드 상태 확인 중…",
                    "ambiguous": False,
                },
                generation,
            )
            self._show_info("FW", "FW 업그레이드 명령을 전송했습니다.")
        except Exception as exc:
            if stage == "start":
                # The one non-retried start request may have reached a device
                # that rebooted before replying. Keep the digest lease, but do
                # not call this a confirmed success.
                self._put_ui(
                    "fw_started",
                    index,
                    {
                        "digest": digest,
                        "sent_at": locals().get("sent_at", time.monotonic()),
                        "text": f"시작 명령 응답 불명; 상태 확인 중 ({exc})",
                        "ambiguous": True,
                    },
                    generation,
                )
                self._show_warning(
                    "FW",
                    "FW 시작 명령의 응답이 불명확합니다. 자동 재전송하지 않고 상태를 확인합니다.",
                )
            else:
                if stage == "tftp_ip":
                    prefix = "TFTP IP 설정 실패/응답 불명; FW 시작 명령은 전송하지 않음"
                elif stage == "start_rejected":
                    prefix = "FW 시작 명령을 장치가 거부함"
                elif stage == "connection_changed":
                    prefix = "연결 변경으로 FW 작업 취소"
                elif stage == "shutdown":
                    prefix = "종료 중이어서 FW 작업 취소"
                elif stage == "lease_expired":
                    prefix = "FW 작업 제한시간 초과"
                elif stage == "connection":
                    prefix = "Modbus 연결 해제로 FW 작업 취소"
                else:
                    prefix = "펌웨어 staging 실패"
                event_generation = self._connection_generation[index]
                self._put_ui(
                    "fw_failed",
                    index,
                    {"digest": digest, "text": f"{prefix}: {exc}"},
                    event_generation,
                )
                self._show_error("FW", f"{prefix}\n{exc}")
        finally:
            if command_lock is not None:
                try:
                    command_lock.release()
                except RuntimeError:
                    pass
            self._put_ui(
                "fw_command_done",
                index,
                {"digest": digest},
                self._connection_generation[index],
            )

    def _set_fw_ui(self, index: int, inflight: bool, text: str) -> None:
        state = self.box_states[index]
        state["fw_status_var"].set(text)
        button = state.get("fw_upgrade_btn")
        if button is not None:
            try:
                button.config(
                    state="disabled" if inflight else "normal",
                    text="진행중…" if inflight else "FW 업그레이드 시작",
                )
            except tk.TclError:
                pass

    def update_fw_status(
        self,
        index: int,
        version: int,
        status: int,
        progress_word: int,
        *,
        sample_monotonic: float | None = None,
    ) -> None:
        error_code = (int(status) >> 8) & 0xFF
        progress = int(progress_word) & 0xFF
        remain = (int(progress_word) >> 8) & 0xFF
        current = (version, status, progress, remain)
        upgrading = bool(status & (1 << 2)) or bool(status & (1 << 6))
        success = bool(status & (1 << 0)) or bool(status & (1 << 4))
        failed = bool(status & (1 << 1)) or bool(status & (1 << 5))
        state = self.box_states[index]
        digest = state.get("fw_digest")
        with self._firmware_coordinator_lock:
            claim = self._fw_deadlines.get(index)
        campaign_active = bool(digest and claim is not None and claim[0] == digest)
        started_at = state.get("fw_started_at")
        try:
            sampled_at = float(sample_monotonic)
        except (TypeError, ValueError):
            sampled_at = time.monotonic()
        after_command = bool(
            campaign_active
            and started_at is not None
            and sampled_at >= float(started_at)
        )
        previous = self.last_fw_status[index]
        baseline = state.get("fw_baseline_status")
        changed_from_baseline = baseline is not None and current != baseline
        if previous == current and not (
            upgrading and after_command and not state.get("fw_seen_active")
        ):
            return
        self.last_fw_status[index] = current

        if upgrading:
            if campaign_active and not after_command:
                return
            state["fw_upgrading"] = True
            if campaign_active:
                state["fw_seen_active"] = True
            state["value"] = progress
            state["bar_value"] = progress
            self._set_fw_ui(index, True, f"진행중 {progress}% (남은 {remain}s)")
        elif success and campaign_active:
            if not after_command or not (
                state.get("fw_seen_active") or changed_from_baseline
            ):
                # Many devices leave terminal bits latched. A value observed
                # before the start command, or an unchanged baseline value,
                # cannot prove that this campaign completed.
                return
            state["fw_upgrading"] = False
            state["fw_cmd_inflight"] = False
            state["fw_ambiguous"] = False
            self._cancel_firmware_timeout(index)
            self._release_firmware(index, digest)
            self._set_fw_ui(index, False, "업그레이드 완료")
            campaign_sequence = int(state.get("fw_campaign_sequence", 0))
            state["fw_clear_after_id"] = self._schedule_after(
                3000,
                self._clear_fw_status,
                index,
                digest,
                campaign_sequence,
            )
        elif failed and campaign_active:
            if not after_command or not (
                state.get("fw_seen_active") or changed_from_baseline
            ):
                return
            state["fw_upgrading"] = False
            state["fw_cmd_inflight"] = False
            state["fw_ambiguous"] = False
            self._cancel_firmware_timeout(index)
            self._release_firmware(index, digest)
            self._set_fw_ui(index, False, f"업그레이드 실패 (err={error_code})")
        elif (success or failed) and not campaign_active:
            # Expose a device-reported terminal state without treating it as
            # proof for, or releasing, any local firmware transaction.
            state["fw_upgrading"] = False
            text = (
                "업그레이드 완료" if success else f"업그레이드 실패 (err={error_code})"
            )
            self._set_fw_ui(index, False, text)
        elif not state.get("fw_upgrading"):
            self._set_fw_ui(index, False, "")

    def _clear_fw_status(
        self,
        index: int,
        digest: str | None,
        campaign_sequence: int | None = None,
    ) -> None:
        state = self.box_states[index]
        sequence_matches = (
            campaign_sequence is None
            or state.get("fw_campaign_sequence") == campaign_sequence
        )
        if (
            state.get("fw_digest") == digest
            and not state.get("fw_upgrading")
            and sequence_matches
        ):
            state["fw_status_var"].set("")
        if sequence_matches:
            state["fw_clear_after_id"] = None

    def _run_register_command(
        self, index: int, register: int, value: int, title: str, success: str
    ) -> None:
        if self._stop_event.is_set():
            return
        if self.box_states[index].get("fw_upgrading") or self.box_states[index].get(
            "fw_cmd_inflight"
        ):
            messagebox.showwarning(
                title,
                "펌웨어 업그레이드 중에는 다른 명령을 보낼 수 없습니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        command_lock = self._command_locks[index]
        if not command_lock.acquire(blocking=False):
            messagebox.showwarning(
                title,
                "이 장치에 다른 명령을 전송 중입니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        generation = self._connection_generation[index]

        def worker() -> None:
            try:
                if self._stop_event.is_set():
                    return
                if generation != self._connection_generation[index]:
                    raise RuntimeError("명령 전송 전에 연결이 변경되었습니다.")
                client, lock = self._get_client_and_lock(index)
                if client is None or lock is None:
                    self._show_warning(title, "먼저 Modbus 연결을 해주세요.")
                    return
                with lock:
                    if self._stop_event.is_set():
                        return
                    if generation != self._connection_generation[index]:
                        raise RuntimeError("명령 전송 전에 연결이 변경되었습니다.")
                    with self._objects_lock:
                        if self.clients.get(index) is not client:
                            raise RuntimeError("명령 전송 전에 연결이 해제되었습니다.")
                    address = self.reg_addr(register)
                    response = client.write_register(address=address, value=int(value))
                if self._is_error_response(response) or not self._write_ack_matches(
                    response, address, [int(value)]
                ):
                    raise RuntimeError(response)
                self._show_info(title, success)
            except Exception as exc:
                self._show_error(
                    title,
                    "명령 결과가 실패 또는 불명확합니다. 자동 재전송하지 않았습니다."
                    f"\n{exc}",
                )
            finally:
                command_lock.release()

        try:
            thread = self._start_worker(worker, name=f"gms-command-{index}-{register}")
        except Exception:
            command_lock.release()
            raise
        if thread is None:
            command_lock.release()

    def zero_calibration(self, index: int) -> None:
        if not messagebox.askyesno(
            "ZERO 확인",
            "ZERO 교정을 시작할까요? 현재 가스가 없는 상태인지 확인하세요.",
            parent=self.parent.winfo_toplevel(),
        ):
            return
        self._run_register_command(index, 40092, 1, "ZERO", "ZERO 명령을 전송했습니다.")

    def reboot_device(self, index: int) -> None:
        if self.box_states[index].get("fw_upgrading") or self.box_states[index].get(
            "fw_cmd_inflight"
        ):
            messagebox.showwarning(
                "RST",
                "펌웨어 업그레이드 중에는 재부팅할 수 없습니다.",
                parent=self.parent.winfo_toplevel(),
            )
            return
        if not messagebox.askyesno(
            "RST 확인",
            "장치를 재부팅할까요? 통신이 잠시 중단됩니다.",
            parent=self.parent.winfo_toplevel(),
        ):
            return
        self._run_register_command(
            index, 40093, 1, "RST", "재부팅 명령을 전송했습니다."
        )

    def change_device_model(self, index: int, model_value: int) -> None:
        name = self.MODEL_VALUE_TO_NAME.get(int(model_value), str(model_value))
        if not messagebox.askyesno(
            "모델 변경",
            f"장치 모델을 {name}(으)로 변경합니다. 진행할까요?",
            parent=self.parent.winfo_toplevel(),
        ):
            return
        self._run_register_command(
            index,
            self.MODEL_SELECT_REG,
            int(model_value),
            "MODEL",
            f"모델 변경 명령을 전송했습니다. ({name})",
        )

    def open_settings_popup(self, index: int) -> None:
        if self._stop_event.is_set():
            return
        self._request_authorization(
            lambda i=index: self._open_settings_popup_authorized(i)
        )

    def _open_settings_popup_authorized(self, index: int) -> None:
        if self._stop_event.is_set() or not 0 <= index < self.num_boxes:
            return
        existing = self.settings_popups[index]
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        win = tk.Toplevel(self.parent)
        self.settings_popups[index] = win
        win.title(f"Box {index + 1} 설정")
        win.configure(bg="#1e1e1e")
        win.resizable(False, False)
        win.transient(self.parent.winfo_toplevel())

        def close() -> None:
            self.settings_popups[index] = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close)
        tk.Label(
            win,
            text=f"IP: {self.ip_vars[index].get() or '미입력'}",
            fg="white",
            bg="#1e1e1e",
            font=("Helvetica", 12, "bold"),
        ).pack(padx=10, pady=(10, 5))
        tk.Label(win, text="TFTP 서버 IP", fg="white", bg="#1e1e1e").pack()
        tk.Entry(
            win, textvariable=self.tftp_ip_vars[index], justify="center", width=18
        ).pack(pady=(0, 8))
        tk.Label(win, text="현재 FW 파일", fg="white", bg="#1e1e1e").pack()
        tk.Label(
            win,
            textvariable=self.box_states[index]["fw_file_name_var"],
            fg="#cccccc",
            bg="#1e1e1e",
        ).pack(pady=(0, 5))
        tk.Label(
            win,
            textvariable=self.box_states[index]["fw_status_var"],
            fg="#ffd966",
            bg="#1e1e1e",
            font=("Helvetica", 10, "bold"),
        ).pack(pady=(0, 8))

        buttons = tk.Frame(win, bg="#1e1e1e")
        buttons.pack(padx=10, pady=8)
        tk.Button(
            buttons,
            text="FW 파일 선택",
            command=lambda: self.select_fw_file(index),
            width=18,
        ).grid(row=0, column=0, padx=4, pady=4)
        fw_button = tk.Button(
            buttons,
            text="FW 업그레이드 시작",
            command=lambda: self.start_firmware_upgrade(index),
            width=18,
        )
        fw_button.grid(row=0, column=1, padx=4, pady=4)
        self.box_states[index]["fw_upgrade_btn"] = fw_button
        tk.Button(
            buttons, text="ZERO", command=lambda: self.zero_calibration(index), width=18
        ).grid(row=1, column=0, padx=4, pady=4)
        tk.Button(
            buttons, text="RST", command=lambda: self.reboot_device(index), width=18
        ).grid(row=1, column=1, padx=4, pady=4)
        tk.Button(
            buttons,
            text="ASGD3200",
            command=lambda: self.change_device_model(index, 0),
            width=18,
        ).grid(row=2, column=0, padx=4, pady=4)
        tk.Button(
            buttons,
            text="ASGD3210",
            command=lambda: self.change_device_model(index, 1),
            width=18,
        ).grid(row=2, column=1, padx=4, pady=4)
        tk.Button(win, text="닫기", command=close, width=10).pack(pady=(0, 10))
        self._schedule_after(
            50,
            lambda: win.focus_force() if win.winfo_exists() else None,
        )

    # Compatibility wrappers used by older callers.
    def detect_device_capabilities(self, ip: str, box_index: int) -> None:
        if self._stop_event.is_set() or not 0 <= box_index < self.num_boxes:
            return
        try:
            detector_ip = normalize_ipv4(ip)
        except ValueError:
            return
        generation = self._connection_generation[box_index]
        existing_client, existing_lock = self._get_client_and_lock(box_index)
        if existing_client is not None:
            try:
                if existing_lock is None:
                    caps = self._probe_capabilities(existing_client, box_index)
                else:
                    with existing_lock:
                        caps = self._probe_capabilities(existing_client, box_index)
                # Preserve the legacy wrapper's connection-state event when it
                # probes the already registered monitoring client.
                self._put_ui("connected", box_index, caps, generation)
            except Exception as exc:
                LOGGER.warning("Modbus capability probe failed: %s", exc)
            return

        def worker() -> None:
            client = None
            try:
                client = ModbusTcpClient(detector_ip, port=502, timeout=3, retries=0)
                if not client.connect():
                    raise ConnectionException(f"{detector_ip}:502 연결 실패")
                caps = self._probe_capabilities(client, box_index)
                self._put_ui("capabilities", box_index, caps, generation)
            except Exception as exc:
                LOGGER.warning("Modbus capability probe failed: %s", exc)
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass

        self._start_worker(worker, name=f"gms-capabilities-{box_index}")

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        generation = self._connection_generation[box_index]
        last_poll = 0.0
        while not stop_flag.is_set() and not self._stop_event.is_set():
            sample, last_poll = self._read_sample(client, box_index, last_poll)
            self._put_ui("sample", box_index, sample, generation)
            stop_flag.wait(self.COMMUNICATION_INTERVAL)

    def load_tftp_ip_from_device(self, box_index: int) -> None:
        client, lock = self._get_client_and_lock(box_index)
        if client is None or lock is None:
            return
        with lock:
            response = client.read_holding_registers(
                address=self.reg_addr(40088), count=2
            )
        registers = self._registers(response)
        if self._is_error_response(response) or len(registers) != 2:
            raise RuntimeError(f"TFTP IP 읽기 실패: {response}")
        value = validate_tftp_ipv4(registers_to_ipv4(registers))
        self._put_ui("tftp_ip", box_index, value)

    def delayed_load_tftp_ip_from_device(
        self, box_index: int, delay: float = 1.0
    ) -> None:
        if self._stop_event.wait(max(0.0, delay)):
            return
        try:
            self.load_tftp_ip_from_device(box_index)
        except Exception as exc:
            self._show_warning("TFTP IP", f"장치 TFTP IP를 읽지 못했습니다.\n{exc}")

    # ------------------------------------------------------------------
    # Shutdown and standalone demo
    # ------------------------------------------------------------------
    def stop(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._stop_event.set()
        with self._after_lock:
            after_ids = list(self._after_ids)
            self._after_ids.clear()
        for after_id in after_ids:
            try:
                self.parent.after_cancel(after_id)
            except tk.TclError:
                pass
        self._ui_after_id = None
        self._blink_after_id = None
        try:
            self.virtual_keyboard.stop()
        except Exception:
            pass
        with self._objects_lock:
            flags = list(self.stop_flags.values())
            connection_threads = list(self.connected_clients.values())
        for flag in flags:
            flag.set()
        with self._worker_lock:
            worker_threads = list(self._worker_threads)
        deadline = time.monotonic() + 8.0
        for thread in dict.fromkeys(connection_threads + worker_threads):
            if thread.is_alive() and thread is not threading.current_thread():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(timeout=remaining)
        for index in list(self._fw_deadlines):
            self._release_firmware(index)
        with self._sample_lock:
            self._pending_samples.clear()
            self._last_enqueued_safety.clear()
        for viewer in self.log_viewers:
            if viewer is not None:
                try:
                    viewer.close()
                except Exception:
                    pass
        for popup in self.settings_popups:
            if popup is not None:
                try:
                    popup.destroy()
                except Exception:
                    pass


def main() -> None:
    root = tk.Tk()
    root.title("Modbus UI")
    root.geometry("1200x700")
    ui = ModbusUI(
        root,
        4,
        {
            "modbus_box_0": "ORG",
            "modbus_box_1": "ARF-T",
            "modbus_box_2": "HMDS",
            "modbus_box_3": "HC-100",
        },
        lambda active, box_id, fut=False: print(box_id, active, fut),
    )
    for index, frame in enumerate(ui.box_frames):
        frame.grid(row=index // 2, column=index % 2, padx=5, pady=5)
    root.protocol("WM_DELETE_WINDOW", lambda: (ui.stop(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
