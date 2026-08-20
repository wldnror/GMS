"""Modbus TCP monitoring and maintenance UI for GMS detector boxes."""

from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from common import BIT_TO_SEGMENT, SEGMENTS, SEGMENT_OFF, SEGMENT_ON, create_gradient_bar, create_segment_display
from core_utils import (
    decode_error_register,
    ip_to_register_words,
    normalize_ipv4,
    register_value,
    registers_to_ipv4,
)
from log_viewer import LogViewer
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

try:
    from rich.console import Console
except Exception:  # pragma: no cover
    class Console:  # type: ignore[no-redef]
        def print(self, *args, **kwargs):
            print(*args)

SCALE_FACTOR = 1.65
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TFTP_IP = "127.0.0.1"
TFTP_ROOT_DIR = Path("/srv/tftp")
TFTP_DEVICE_SUBDIR = Path("GDS") / "ASGD-3200"
TFTP_DEVICE_FILENAME = "asgd3200.bin"


def sx(value: float) -> int:
    return int(value * SCALE_FACTOR)


def sy(value: float) -> int:
    return int(value * SCALE_FACTOR)


def get_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.5)
        sock.connect(("8.8.8.8", 80))
        return normalize_ipv4(sock.getsockname()[0])
    except Exception:
        return DEFAULT_TFTP_IP
    finally:
        sock.close()


class ModbusUI:
    SETTINGS_FILE = BASE_DIR / "modbus_settings.json"
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
    MAX_RECONNECT_ATTEMPTS = 5

    @staticmethod
    def reg_addr(register: int) -> int:
        return int(register) - 40001

    def __init__(self, parent, num_boxes: int, gas_types: dict, alarm_callback):
        self.parent = parent
        self.num_boxes = max(0, int(num_boxes))
        self.alarm_callback = alarm_callback
        self.console = Console()
        self.virtual_keyboard = VirtualKeyboard(parent)
        self.virtual_keyboard.set_num_boxes(self.num_boxes)

        self.ip_vars = [tk.StringVar() for _ in range(self.num_boxes)]
        self.tftp_ip_vars = [tk.StringVar(value=get_local_ip()) for _ in range(self.num_boxes)]
        self.fw_file_paths: list[str | None] = [None] * self.num_boxes
        self.entries: list[tk.Entry] = []
        self.action_buttons: list[tk.Button] = []
        self.box_frames: list[tk.Frame] = []
        self.box_data: list[tuple[tk.Canvas, list[int], tk.Canvas, int]] = []
        self.box_states: list[dict] = []
        self.settings_popups: list[tk.Toplevel | None] = [None] * self.num_boxes
        self.log_viewers: list[LogViewer | None] = [None] * self.num_boxes
        self.box_logs: list[list[tuple]] = [[] for _ in range(self.num_boxes)]
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
        self.ui_queue: queue.Queue[tuple[str, int, object]] = queue.Queue(maxsize=1000)
        self._ui_after_id: str | None = None
        self._blink_after_id: str | None = None
        self._blink_phase = False

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

        self._ui_after_id = self.parent.after(100, self._process_ui_queue)
        self._blink_after_id = self.parent.after(500, self._blink_tick)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def _load_images(self) -> None:
        self.connect_image = self._load_image(BASE_DIR / "img" / "on.png", (sx(50), sy(70)), "#3b8f3b")
        self.disconnect_image = self._load_image(BASE_DIR / "img" / "off.png", (sx(50), sy(70)), "#9d3d3d")

    def _load_image(self, path: Path, size: tuple[int, int], fallback: str):
        try:
            image = Image.open(path).convert("RGBA")
            image.thumbnail(size, Image.Resampling.LANCZOS)
        except Exception:
            image = Image.new("RGBA", size, fallback)
        return ImageTk.PhotoImage(image)

    def _put_ui(self, event_type: str, index: int, payload: object = None) -> None:
        item = (event_type, index, payload)
        try:
            self.ui_queue.put_nowait(item)
        except queue.Full:
            try:
                self.ui_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.ui_queue.put_nowait(item)
            except queue.Full:
                pass

    def _ui_call(self, callback, *args, **kwargs) -> None:
        try:
            self.parent.after(0, lambda: callback(*args, **kwargs))
        except tk.TclError:
            pass

    def _show_info(self, title: str, text: str) -> None:
        self._ui_call(messagebox.showinfo, title, text, parent=self.parent.winfo_toplevel())

    def _show_warning(self, title: str, text: str) -> None:
        self._ui_call(messagebox.showwarning, title, text, parent=self.parent.winfo_toplevel())

    def _show_error(self, title: str, text: str) -> None:
        self._ui_call(messagebox.showerror, title, text, parent=self.parent.winfo_toplevel())

    @staticmethod
    def _is_error_response(response) -> bool:
        if response is None:
            return True
        if ExceptionResponse and isinstance(response, ExceptionResponse):
            return True
        try:
            return bool(response.isError())
        except Exception:
            return False

    @staticmethod
    def _registers(response) -> list[int]:
        values = getattr(response, "registers", None)
        return [int(value) for value in values] if values else []

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
            self.alarm_callback(active, box_id)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def load_ip_settings(self) -> None:
        if not self.SETTINGS_FILE.exists():
            return
        try:
            values = json.loads(self.SETTINGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(values, list):
                return
            for index, value in enumerate(values[: self.num_boxes]):
                try:
                    self.ip_vars[index].set(normalize_ipv4(value))
                except ValueError:
                    continue
        except (OSError, json.JSONDecodeError):
            return

    def save_ip_settings(self) -> None:
        values = [variable.get().strip() for variable in self.ip_vars]
        temp = self.SETTINGS_FILE.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.SETTINGS_FILE)
        except OSError as exc:
            print(f"Modbus IP 설정 저장 오류: {exc}")
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

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
        canvas.create_rectangle(0, sy(200), sx(160), sy(310), fill="black", outline="black")
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
            sx(140), sy(12), text="", font=("Helvetica", sx(8), "bold"), fill="#cccccc", anchor="ne"
        )
        badge_bg = canvas.create_rectangle(
            sx(6), sy(6), sx(55), sy(20), fill="#2b2b2b", outline="#444444", state="hidden"
        )
        badge_text = canvas.create_text(
            sx(10), sy(8), text="LOG 0", font=("Helvetica", sx(8), "bold"), fill="#ffd966", anchor="nw", state="hidden"
        )

        circles = [
            canvas.create_oval(sx(57), sy(158), sx(67), sy(168)),
            canvas.create_oval(sx(93), sy(158), sx(103), sy(168)),
            canvas.create_oval(sx(20), sy(158), sx(30), sy(168)),
            canvas.create_oval(sx(131), sy(158), sx(141), sy(168)),
        ]
        for x, label in ((62, "AL1"), (98, "AL2"), (25, "PWR"), (136, "FUT")):
            canvas.create_text(sx(x), sy(182), text=label, fill="#cccccc", font=("Helvetica", sx(8)))

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
        entry.bind("<Button-1>", lambda _event, widget=entry: self.virtual_keyboard.show(widget), add="+")
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
        dc_label = tk.Label(control, text="DC: 0", fg="white", bg="black", font=("Helvetica", sx(8)))
        dc_label.grid(row=1, column=0, columnspan=2)
        reconnect_label = tk.Label(control, text="Reconnect: 0/5", fg="yellow", bg="black", font=("Helvetica", sx(8)))
        reconnect_label.grid(row=2, column=0, columnspan=2)
        dc_label.grid_remove()
        reconnect_label.grid_remove()

        gms_text = canvas.create_text(
            sx(80), sy(270), text="GMS-1000", font=("Helvetica", sx(16), "bold"), fill="#cccccc"
        )
        canvas.create_text(
            sx(80), sy(295), text="GDS ENGINEERING CO.,LTD", font=("Helvetica", sx(7), "bold"), fill="#cccccc"
        )

        bar_canvas = tk.Canvas(canvas, width=self._gradient_width, height=self.gradient_bar.height, bg="black", highlightthickness=0)
        bar_canvas.place(x=sx(18), y=sy(75))
        bar_item = bar_canvas.create_image(0, 0, anchor="nw", state="hidden")

        click_area = canvas.create_rectangle(sx(10), sy(25), sx(140), sy(90), outline="", fill="")
        canvas.tag_bind(click_area, "<Button-1>", lambda _event, i=index: self.open_log_viewer(i))
        canvas.segment_canvas.bind("<Button-1>", lambda _event, i=index: self.open_log_viewer(i))
        for circle in circles:
            canvas.tag_bind(circle, "<Button-1>", lambda _event, i=index: self.open_settings_popup(i))

        state = {
            "connected": False,
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
            self.disconnect(index, manual=True)
        else:
            self.connect(index)

    def connect(self, index: int) -> None:
        if self._stop_event.is_set() or index in self.connected_clients:
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
            messagebox.showwarning("IP 주소", str(exc), parent=self.parent.winfo_toplevel())
            return
        self.ip_vars[index].set(ip)
        self.save_ip_settings()
        stop_flag = threading.Event()
        thread = threading.Thread(
            target=self._connection_worker,
            args=(index, ip, stop_flag),
            name=f"gms-modbus-{index}",
            daemon=True,
        )
        with self._objects_lock:
            self.stop_flags[index] = stop_flag
            self.connected_clients[index] = thread
            self.modbus_locks[index] = threading.Lock()
        self.entries[index].config(state="disabled")
        self.reconnect_attempt_labels[index].config(text="Connecting: 1/5")
        self.reconnect_attempt_labels[index].grid()
        thread.start()

    def _connection_worker(self, index: int, ip: str, stop_flag: threading.Event) -> None:
        attempt = 0
        ever_connected = False
        while not self._stop_event.is_set() and not stop_flag.is_set():
            attempt += 1
            self._put_ui("connecting", index, attempt)
            client = ModbusTcpClient(ip, port=502, timeout=3)
            try:
                if not client.connect():
                    raise ConnectionException(f"{ip}:502 연결 실패")
                with self._objects_lock:
                    self.clients[index] = client
                capabilities = self._probe_capabilities(client, index)
                self._put_ui("connected", index, capabilities)
                ever_connected = True
                attempt = 0
                last_sensor_poll = 0.0
                while not self._stop_event.is_set() and not stop_flag.is_set():
                    sample, last_sensor_poll = self._read_sample(
                        client, index, last_sensor_poll
                    )
                    self._put_ui("sample", index, sample)
                    if stop_flag.wait(self.COMMUNICATION_INTERVAL):
                        break
            except Exception as exc:
                if not stop_flag.is_set() and not self._stop_event.is_set():
                    self._put_ui("connection_error", index, str(exc))
            finally:
                try:
                    client.close()
                except Exception:
                    pass
                with self._objects_lock:
                    if self.clients.get(index) is client:
                        self.clients.pop(index, None)
            if stop_flag.is_set() or self._stop_event.is_set():
                break
            if ever_connected:
                self._put_ui("disconnected", index, None)
                ever_connected = False
                attempt = 0
            if attempt >= self.MAX_RECONNECT_ATTEMPTS:
                self._put_ui("failed", index, None)
                break
            stop_flag.wait(2.0)

        with self._objects_lock:
            self.clients.pop(index, None)
            self.connected_clients.pop(index, None)
            self.modbus_locks.pop(index, None)
            self.stop_flags.pop(index, None)
        self._put_ui("stopped", index, None)

    def _probe_capabilities(self, client, index: int) -> dict:
        base_response = client.read_holding_registers(
            address=self.reg_addr(40001), count=22
        )
        if self._is_error_response(base_response) or len(self._registers(base_response)) < 22:
            raise ModbusIOException(f"기본 레지스터 읽기 실패: {base_response}")

        extended = False
        try:
            response = client.read_holding_registers(
                address=self.reg_addr(40001), count=24
            )
            extended = not self._is_error_response(response) and len(self._registers(response)) >= 24
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
            if not self._is_error_response(response) and len(registers) == self.SENSOR_MODEL_REG_COUNT:
                sensor_model = self.regs_to_ascii(registers)
                sensor_supported = True
        except Exception:
            pass

        tftp_ip = None
        if extended:
            try:
                response = client.read_holding_registers(
                    address=self.reg_addr(40088), count=2
                )
                registers = self._registers(response)
                if not self._is_error_response(response) and len(registers) == 2:
                    tftp_ip = registers_to_ipv4(registers)
            except Exception:
                pass
        return {
            "extended": extended,
            "fw_status": extended,
            "tftp": extended,
            "sensor_supported": sensor_supported,
            "sensor_model": sensor_model,
            "tftp_ip": tftp_ip,
        }

    def _read_sample(self, client, index: int, last_sensor_poll: float) -> tuple[dict, float]:
        count = 24 if self.extended_supported[index] else 22
        with self._objects_lock:
            lock = self.modbus_locks.setdefault(index, threading.Lock())
        with lock:
            response = client.read_holding_registers(
                address=self.reg_addr(40001), count=count
            )
        raw_regs = self._registers(response)
        if self._is_error_response(response):
            if count == 24:
                self.extended_supported[index] = False
                self.fw_status_supported[index] = False
                self.tftp_supported[index] = False
                return self._read_sample(client, index, last_sensor_poll)
            raise ModbusIOException(f"레지스터 읽기 실패: {response}")
        if len(raw_regs) < 22:
            raise ModbusIOException(f"레지스터 개수 부족: {len(raw_regs)}")

        error_reg = register_value(raw_regs, 40007)  # 40007 is index 6.
        value_40001 = register_value(raw_regs, 40001)
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
        }
        if len(raw_regs) >= 24 and self.fw_status_supported[index]:
            sample["fw"] = (
                register_value(raw_regs, 40022),
                register_value(raw_regs, 40023),
                register_value(raw_regs, 40024),
            )

        now = time.monotonic()
        if self.sensor_model_supported[index] and now - last_sensor_poll >= self.SENSOR_MODEL_POLL_SEC:
            last_sensor_poll = now
            try:
                with lock:
                    model_response = client.read_holding_registers(
                        address=self.reg_addr(self.SENSOR_MODEL_REG),
                        count=self.SENSOR_MODEL_REG_COUNT,
                    )
                model_registers = self._registers(model_response)
                if not self._is_error_response(model_response):
                    sample["sensor_model"] = self.regs_to_ascii(model_registers)
            except Exception:
                pass
        return sample, last_sensor_poll

    def disconnect(self, index: int, manual: bool = False) -> None:
        with self._objects_lock:
            flag = self.stop_flags.get(index)
            client = self.clients.get(index)
        if flag:
            flag.set()
        if client:
            try:
                client.close()
            except Exception:
                pass
        if manual:
            self._put_ui("manual_disconnect", index, None)

    def disconnect_client(self, ip_or_index, index: int | None = None, manual: bool = False) -> None:
        target = int(ip_or_index) if index is None else int(index)
        self.disconnect(target, manual=manual)

    def cleanup_client(self, ip_or_index) -> None:
        try:
            index = int(ip_or_index)
        except (TypeError, ValueError):
            return
        with self._objects_lock:
            self.clients.pop(index, None)
            self.connected_clients.pop(index, None)
            self.stop_flags.pop(index, None)
            self.modbus_locks.pop(index, None)

    def connect_to_server(self, ip: str, client) -> bool:
        for _ in range(self.MAX_RECONNECT_ATTEMPTS):
            if client.connect():
                return True
            time.sleep(2)
        return False

    # ------------------------------------------------------------------
    # UI queue handling
    # ------------------------------------------------------------------
    def _process_ui_queue(self) -> None:
        self._ui_after_id = None
        if self._stop_event.is_set():
            return
        try:
            while True:
                event_type, index, payload = self.ui_queue.get_nowait()
                self._handle_ui_event(event_type, index, payload)
        except queue.Empty:
            pass
        try:
            self._ui_after_id = self.parent.after(100, self._process_ui_queue)
        except tk.TclError:
            self._stop_event.set()

    def _handle_ui_event(self, event_type: str, index: int, payload: object) -> None:
        if not 0 <= index < self.num_boxes:
            return
        state = self.box_states[index]
        if event_type == "connecting":
            attempt = int(payload)
            label = self.reconnect_attempt_labels[index]
            if label:
                label.config(text=f"Reconnect: {attempt}/{self.MAX_RECONNECT_ATTEMPTS}")
                label.grid()
        elif event_type == "connected":
            capabilities = dict(payload) if isinstance(payload, dict) else {}
            state["connected"] = True
            state["ip"] = self.ip_vars[index].get()
            self.extended_supported[index] = bool(capabilities.get("extended"))
            self.fw_status_supported[index] = bool(capabilities.get("fw_status"))
            self.tftp_supported[index] = bool(capabilities.get("tftp"))
            self.sensor_model_supported[index] = bool(capabilities.get("sensor_supported"))
            state["sensor_model"] = str(capabilities.get("sensor_model") or "")
            tftp_ip = capabilities.get("tftp_ip")
            if tftp_ip:
                self.tftp_ip_vars[index].set(str(tftp_ip))
            self.action_buttons[index].config(image=self.disconnect_image)
            self.entries[index].config(state="disabled")
            self.disconnection_labels[index].grid()
            self.reconnect_attempt_labels[index].config(text="Reconnect: OK")
            self.reconnect_attempt_labels[index].grid()
            self.box_data[index][0].itemconfig(state["gms_text_id"], state="hidden")
            self.show_bar(index, True)
            self._render_box(index)
        elif event_type == "sample" and isinstance(payload, dict):
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
                self.update_fw_status(index, version, status, progress)
        elif event_type in ("disconnected", "connection_error"):
            if state["connected"]:
                self.disconnection_counts[index] += 1
            state["connected"] = False
            label = self.disconnection_labels[index]
            if label:
                label.config(text=f"DC: {self.disconnection_counts[index]}")
                label.grid()
            self._reset_display_state(index)
        elif event_type == "failed":
            state["connected"] = False
            self.reconnect_attempt_labels[index].config(text="Reconnect: Failed")
            self.entries[index].config(state="normal")
            self.action_buttons[index].config(image=self.connect_image)
            self._reset_display_state(index)
        elif event_type in ("stopped", "manual_disconnect"):
            state["connected"] = False
            self.entries[index].config(state="normal")
            self.action_buttons[index].config(image=self.connect_image)
            if event_type == "manual_disconnect":
                self.disconnection_labels[index].grid_remove()
                self.reconnect_attempt_labels[index].grid_remove()
                self.box_data[index][0].itemconfig(state["gms_text_id"], state="normal")
            self._reset_display_state(index)
        elif event_type == "fw_message" and isinstance(payload, tuple):
            inflight, text = payload
            self._set_fw_ui(index, bool(inflight), str(text))
        elif event_type == "capability_disabled":
            self.extended_supported[index] = False
            self.fw_status_supported[index] = False
            self.tftp_supported[index] = False

    def _reset_display_state(self, index: int) -> None:
        state = self.box_states[index]
        state.update(
            {
                "value": 0,
                "bar_value": 0,
                "bar_render_key": None,
                "alarm1": False,
                "alarm2": False,
                "error_reg": 0,
                "error_display": "",
                "version": None,
                "sensor_model": "",
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
            self._render_box(index)
        try:
            self._blink_after_id = self.parent.after(500, self._blink_tick)
        except tk.TclError:
            self._stop_event.set()

    def _render_box(self, index: int) -> None:
        state = self.box_states[index]
        canvas, circles, _bar_canvas, _bar_item = self.box_data[index]
        connected = bool(state["connected"])
        error_active = bool(state["error_display"])
        alarm1 = connected and bool(state["alarm1"]) and not error_active
        alarm2 = connected and bool(state["alarm2"]) and not error_active

        display = state["error_display"] if error_active else str(state["value"])
        if not connected or (error_active and not self._blink_phase):
            display = "    "
        self.update_segment_display(display, index)
        self.update_bar(state["bar_value"], index)

        al1_on = alarm2 or (alarm1 and self._blink_phase)
        al2_on = alarm2 and self._blink_phase
        pwr_on = connected and self._blink_phase
        fut_on = error_active and self._blink_phase
        colors = [
            "red" if al1_on else self.LAMP_COLORS_OFF[0],
            "red" if al2_on else self.LAMP_COLORS_OFF[1],
            "green" if pwr_on else self.LAMP_COLORS_OFF[2],
            "yellow" if fut_on else self.LAMP_COLORS_OFF[3],
        ]
        for item, color in zip(circles, colors):
            canvas.itemconfig(item, fill=color, outline=color)
        if error_active:
            border = "#ffff00" if self._blink_phase else "#000000"
        elif alarm1 or alarm2:
            border = "#ff0000" if self._blink_phase else "#000000"
        else:
            border = "#000000"
        self.box_frames[index].config(highlightbackground=border)
        self._update_topright_label(index)
        self._notify_alarm(alarm1 or alarm2, f"modbus_{index}", error_active)

    def update_segment_display(self, value, box_index: int = 0, blink: bool = False) -> None:
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
        image = ImageTk.PhotoImage(self.gradient_bar.crop((0, 0, width, self.gradient_bar.height)))
        canvas.itemconfig(item, image=image, state="normal")
        canvas._bar_image = image  # type: ignore[attr-defined]

    def show_bar(self, box_index: int, show: bool) -> None:
        canvas = self.box_data[box_index][2]
        item = self.box_data[box_index][3]
        canvas.itemconfig(item, state="normal" if show and self.box_states[box_index]["bar_value"] else "hidden")

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
        text = f"{version_text} / {model}" if version_text and model else version_text or model
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
    def maybe_log_event(self, index: int, value: int, alarm1: bool, alarm2: bool, error_reg: int) -> None:
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
        state["last_log_value"], state["last_log_alarm1"], state["last_log_alarm2"], state["last_log_error"] = current
        entry = (time.strftime("%Y-%m-%d %H:%M:%S"), *current)
        logs = self.box_logs[index]
        logs.append(entry)
        if len(logs) > self.LOG_MAX_ENTRIES:
            del logs[: len(logs) - self.LOG_MAX_ENTRIES]
        self.update_log_badge(index)

    def update_log_badge(self, index: int) -> None:
        state = self.box_states[index]
        canvas = self.box_data[index][0]
        unread = max(0, len(self.box_logs[index]) - self.last_viewed_log_len[index])
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
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        self.last_viewed_log_len[index] = len(self.box_logs[index])
        self.update_log_badge(index)

        def clear() -> None:
            self.box_logs[index].clear()
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
    def select_fw_file(self, index: int) -> None:
        path = filedialog.askopenfilename(
            parent=self.parent.winfo_toplevel(),
            title="FW 파일 선택",
            filetypes=[("BIN files", "*.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        self.fw_file_paths[index] = path
        self.box_states[index]["fw_file_name_var"].set(Path(path).name)

    def select_fw_file_all(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.parent.winfo_toplevel(),
            title="FW 파일 선택(전체 적용)",
            filetypes=[("BIN files", "*.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        for index in range(self.num_boxes):
            self.fw_file_paths[index] = path
            self.box_states[index]["fw_file_name_var"].set(Path(path).name)
        messagebox.showinfo("FW", "선택한 FW 파일을 전체 박스에 적용했습니다.", parent=self.parent.winfo_toplevel())

    def start_firmware_upgrade_all(self, only_connected: bool = True, delay_sec: float = 0.5) -> None:
        targets = [
            index
            for index in range(self.num_boxes)
            if self.fw_file_paths[index]
            and (not only_connected or self.box_states[index]["connected"])
            and self.tftp_supported[index]
            and not self.box_states[index]["fw_cmd_inflight"]
        ]
        selected_files = {str(Path(self.fw_file_paths[index]).resolve()) for index in targets if self.fw_file_paths[index]}
        if len(selected_files) > 1:
            messagebox.showwarning(
                "FW",
                "일괄 업데이트는 모든 장치에 동일한 FW 파일이 선택된 경우에만 가능합니다.",
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
            f"{len(targets)}개 장치에 FW 업그레이드 명령을 순차 전송합니다. 진행할까요?",
            parent=self.parent.winfo_toplevel(),
        ):
            return
        for offset, index in enumerate(targets):
            self.parent.after(int(max(0.0, delay_sec) * 1000 * offset), lambda i=index: self.start_firmware_upgrade(i))

    def start_firmware_upgrade(self, index: int) -> None:
        state = self.box_states[index]
        if not state["connected"]:
            messagebox.showwarning("FW", "먼저 Modbus 연결을 해주세요.", parent=self.parent.winfo_toplevel())
            return
        if not self.tftp_supported[index]:
            messagebox.showwarning("FW", "이 장치는 FW/TFTP 기능을 지원하지 않습니다.", parent=self.parent.winfo_toplevel())
            return
        if state["fw_cmd_inflight"]:
            return
        source = self.fw_file_paths[index]
        if not source or not Path(source).is_file():
            messagebox.showwarning("FW", "FW 파일을 먼저 선택해주세요.", parent=self.parent.winfo_toplevel())
            return
        try:
            tftp_ip = normalize_ipv4(self.tftp_ip_vars[index].get())
        except ValueError as exc:
            messagebox.showwarning("TFTP IP", str(exc), parent=self.parent.winfo_toplevel())
            return
        state["fw_cmd_inflight"] = True
        self._set_fw_ui(index, True, "명령 전송 중…")
        threading.Thread(
            target=self._firmware_worker,
            args=(index, source, tftp_ip),
            daemon=True,
            name=f"gms-fw-{index}",
        ).start()

    def _get_client_and_lock(self, index: int):
        with self._objects_lock:
            return self.clients.get(index), self.modbus_locks.get(index)

    def _firmware_worker(self, index: int, source: str, tftp_ip: str) -> None:
        state = self.box_states[index]
        try:
            destination_dir = TFTP_ROOT_DIR / TFTP_DEVICE_SUBDIR
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / TFTP_DEVICE_FILENAME
            temp = destination.with_name(f".{destination.name}.{index}.tmp")
            shutil.copyfile(source, temp)
            os.replace(temp, destination)

            client, lock = self._get_client_and_lock(index)
            if client is None or lock is None:
                raise RuntimeError("Modbus 연결이 해제되었습니다.")
            word1, word2 = ip_to_register_words(tftp_ip)
            with lock:
                response = client.write_registers(
                    address=self.reg_addr(40088), values=[word1, word2]
                )
                if self._is_error_response(response):
                    raise RuntimeError(f"TFTP IP 쓰기 실패: {response}")
                response = client.write_register(
                    address=self.reg_addr(40091), value=1
                )
                if self._is_error_response(response):
                    raise RuntimeError(f"FW 시작 명령 실패: {response}")
            state["fw_upgrading"] = True
            self._put_ui("fw_message", index, (True, "명령 전송 완료. 업그레이드 진행 중…"))
            self._show_info("FW", "FW 업그레이드 명령을 전송했습니다.")
        except Exception as exc:
            # A reboot immediately after the write can prevent a valid response.
            text = str(exc)
            acceptable = ("No response received", "Invalid Message", "Unable to decode response")
            if any(word in text for word in acceptable):
                state["fw_upgrading"] = True
                self._put_ui("fw_message", index, (True, "업그레이드 진행 중…"))
                self._show_info("FW", "장비 재시작으로 응답이 끊겼습니다. 업그레이드 상태를 계속 확인합니다.")
            else:
                self._put_ui("fw_message", index, (False, f"실패: {exc}"))
                self._show_error("FW", f"FW 업그레이드 명령 전송 실패\n{exc}")
        finally:
            state["fw_cmd_inflight"] = False

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

    def update_fw_status(self, index: int, version: int, status: int, progress_word: int) -> None:
        error_code = (int(status) >> 8) & 0xFF
        progress = int(progress_word) & 0xFF
        remain = (int(progress_word) >> 8) & 0xFF
        current = (version, status, progress, remain)
        if self.last_fw_status[index] == current:
            return
        self.last_fw_status[index] = current
        upgrading = bool(status & (1 << 2))
        success = bool(status & (1 << 0)) or bool(status & (1 << 4))
        failed = bool(status & (1 << 1)) or bool(status & (1 << 5))
        self.box_states[index]["fw_upgrading"] = upgrading
        if upgrading:
            self.box_states[index]["value"] = progress
            self.box_states[index]["bar_value"] = progress
            self._set_fw_ui(index, True, f"진행중 {progress}% (남은 {remain}s)")
        elif success:
            self._set_fw_ui(index, False, "업그레이드 완료")
            self.parent.after(3000, lambda i=index: self.box_states[i]["fw_status_var"].set(""))
        elif failed:
            self._set_fw_ui(index, False, f"업그레이드 실패 (err={error_code})")
        else:
            self._set_fw_ui(index, False, "")

    def _run_register_command(self, index: int, register: int, value: int, title: str, success: str) -> None:
        def worker() -> None:
            client, lock = self._get_client_and_lock(index)
            if client is None or lock is None:
                self._show_warning(title, "먼저 Modbus 연결을 해주세요.")
                return
            try:
                with lock:
                    response = client.write_register(address=self.reg_addr(register), value=int(value))
                if self._is_error_response(response):
                    raise RuntimeError(response)
                self._show_info(title, success)
            except Exception as exc:
                text = str(exc)
                if register in (40093, self.MODEL_SELECT_REG) and any(
                    marker in text for marker in ("No response received", "Invalid Message")
                ):
                    self._show_info(title, success + "\n장비가 재시작되는 동안 통신이 잠시 끊길 수 있습니다.")
                else:
                    self._show_error(title, f"명령 전송 실패\n{exc}")

        threading.Thread(target=worker, daemon=True).start()

    def zero_calibration(self, index: int) -> None:
        self._run_register_command(index, 40092, 1, "ZERO", "ZERO 명령을 전송했습니다.")

    def reboot_device(self, index: int) -> None:
        self._run_register_command(index, 40093, 1, "RST", "재부팅 명령을 전송했습니다.")

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
        tk.Label(win, text=f"IP: {self.ip_vars[index].get() or '미입력'}", fg="white", bg="#1e1e1e", font=("Helvetica", 12, "bold")).pack(padx=10, pady=(10, 5))
        tk.Label(win, text="TFTP 서버 IP", fg="white", bg="#1e1e1e").pack()
        tk.Entry(win, textvariable=self.tftp_ip_vars[index], justify="center", width=18).pack(pady=(0, 8))
        tk.Label(win, text="현재 FW 파일", fg="white", bg="#1e1e1e").pack()
        tk.Label(win, textvariable=self.box_states[index]["fw_file_name_var"], fg="#cccccc", bg="#1e1e1e").pack(pady=(0, 5))
        tk.Label(win, textvariable=self.box_states[index]["fw_status_var"], fg="#ffd966", bg="#1e1e1e", font=("Helvetica", 10, "bold")).pack(pady=(0, 8))

        buttons = tk.Frame(win, bg="#1e1e1e")
        buttons.pack(padx=10, pady=8)
        tk.Button(buttons, text="FW 파일 선택", command=lambda: self.select_fw_file(index), width=18).grid(row=0, column=0, padx=4, pady=4)
        fw_button = tk.Button(buttons, text="FW 업그레이드 시작", command=lambda: self.start_firmware_upgrade(index), width=18)
        fw_button.grid(row=0, column=1, padx=4, pady=4)
        self.box_states[index]["fw_upgrade_btn"] = fw_button
        tk.Button(buttons, text="ZERO", command=lambda: self.zero_calibration(index), width=18).grid(row=1, column=0, padx=4, pady=4)
        tk.Button(buttons, text="RST", command=lambda: self.reboot_device(index), width=18).grid(row=1, column=1, padx=4, pady=4)
        tk.Button(buttons, text="ASGD3200", command=lambda: self.change_device_model(index, 0), width=18).grid(row=2, column=0, padx=4, pady=4)
        tk.Button(buttons, text="ASGD3210", command=lambda: self.change_device_model(index, 1), width=18).grid(row=2, column=1, padx=4, pady=4)
        tk.Button(win, text="닫기", command=close, width=10).pack(pady=(0, 10))
        win.after(50, lambda: win.focus_force())

    # Compatibility wrappers used by older callers.
    def detect_device_capabilities(self, ip: str, box_index: int) -> None:
        client, _lock = self._get_client_and_lock(box_index)
        if client is None:
            return
        caps = self._probe_capabilities(client, box_index)
        self._put_ui("connected", box_index, caps)

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        last_poll = 0.0
        while not stop_flag.is_set():
            sample, last_poll = self._read_sample(client, box_index, last_poll)
            self._put_ui("sample", box_index, sample)
            stop_flag.wait(self.COMMUNICATION_INTERVAL)

    def load_tftp_ip_from_device(self, box_index: int) -> None:
        client, lock = self._get_client_and_lock(box_index)
        if client is None or lock is None:
            return
        with lock:
            response = client.read_holding_registers(address=self.reg_addr(40088), count=2)
        registers = self._registers(response)
        if self._is_error_response(response) or len(registers) != 2:
            raise RuntimeError(f"TFTP IP 읽기 실패: {response}")
        value = registers_to_ipv4(registers)
        self._ui_call(self.tftp_ip_vars[box_index].set, value)

    def delayed_load_tftp_ip_from_device(self, box_index: int, delay: float = 1.0) -> None:
        time.sleep(max(0.0, delay))
        self.load_tftp_ip_from_device(box_index)

    # ------------------------------------------------------------------
    # Shutdown and standalone demo
    # ------------------------------------------------------------------
    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        for after_id in (self._ui_after_id, self._blink_after_id):
            if after_id:
                try:
                    self.parent.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._ui_after_id = None
        self._blink_after_id = None
        self.virtual_keyboard.stop()
        with self._objects_lock:
            flags = list(self.stop_flags.values())
            clients = list(self.clients.values())
            threads = list(self.connected_clients.values())
        for flag in flags:
            flag.set()
        for client in clients:
            try:
                client.close()
            except Exception:
                pass
        for thread in threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=3.5)
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
