"""Thread-safe 4–20 mA monitoring panels backed by ADS1115 converters."""

from __future__ import annotations

import csv
import math
import os
import queue
import threading
import time
import tkinter as tk
from collections import deque

import utils
from common import SEGMENT_OFF, SEGMENT_ON, SEGMENTS, create_segment_display
from core_utils import display_engineering_value, format_engineering_value, scale_4_20ma
from gms_core import analog_signal_fault
from log_viewer import LogViewer
from ui_config import UI_SCALE

try:
    import Adafruit_ADS1x15
except Exception:  # pragma: no cover - target hardware dependency
    Adafruit_ADS1x15 = None

GAIN = 2 / 3
SCALE_FACTOR = UI_SCALE


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


class AnalogUI:
    GAS_FULL_SCALE = {
        "ORG": 9999.0,
        "ARF-T": 5000.0,
        "HMDS": 3000.0,
        "HC-100": 5000.0,
    }
    GAS_TYPE_POSITIONS = {
        "ORG": (int(115 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "ARF-T": (int(107 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "HMDS": (int(110 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "HC-100": (int(104 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
    }
    # Thresholds use raw engineering units. HMDS is displayed/logged divided by 10.
    ALARM_LEVELS = {
        "ORG": {"AL1": 9500.0, "AL2": 9999.0},
        "ARF-T": {"AL1": 2000.0, "AL2": 4000.0},
        "HMDS": {"AL1": 2640.0, "AL2": 3000.0},
        "HC-100": {"AL1": 1500.0, "AL2": 3000.0},
    }
    ADC_ADDRESSES = (0x48, 0x49, 0x4B)
    LOG_DIR = utils.STATE_DIR / "analog_logs"
    LOG_MAX_ENTRIES = 1000
    LOG_QUEUE_MAX = 5000
    VALUE_LOG_INTERVAL_SEC = _env_float("GMS_ANALOG_LOG_INTERVAL_SEC", 5.0, 1.0)
    LOG_RETENTION_DAYS = _env_int("GMS_ANALOG_LOG_RETENTION_DAYS", 30, 1)
    LOG_FLUSH_INTERVAL_SEC = 5.0
    ADC_RETRY_SEC = 10.0
    SIGNAL_FAULT_LOW_MA = 3.6
    SIGNAL_FAULT_HIGH_MA = 21.0
    FAULT_CLEAR_SAMPLES = 2

    def __init__(self, parent, num_boxes: int, gas_types: dict, alarm_callback):
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.num_boxes = max(0, int(num_boxes))
        self.box_frames: list[tk.Frame] = []
        self.box_data: list[tuple[tk.Canvas, list[int]]] = []
        self.box_states: list[dict] = []
        self.gas_types: dict[str, tk.StringVar] = {}
        self.adc_values = [deque(maxlen=3) for _ in range(self.num_boxes)]
        self.box_logs: list[list[tuple]] = [[] for _ in range(self.num_boxes)]
        self.log_sequences = [0] * self.num_boxes
        self.last_viewed_sequence = [0] * self.num_boxes
        self.log_viewers: list[LogViewer | None] = [None] * self.num_boxes

        self.log_queue: deque[tuple[str, int, list]] = deque()
        self._log_condition = threading.Condition()
        self.log_dropped_count = 0
        self.log_dropped_critical_count = 0
        self._logging_fault_active = False
        self.logging_available = True
        if self.num_boxes:
            try:
                utils.ensure_state_dir()
                self.LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self.LOG_DIR, 0o700)
            except OSError as exc:
                self.logging_available = False
                print(f"Analog 로그 디렉터리를 사용할 수 없습니다: {exc}")
                self._set_logging_fault(True)
        # ADC faults are low-rate control events; samples are coalesced per
        # channel so a paused Tk loop cannot evict a safety event or grow RAM.
        self.sample_queue: queue.Queue[tuple[str, int, object]] = queue.Queue(
            maxsize=32
        )
        self._sample_lock = threading.Lock()
        self._pending_adc_samples: dict[int, object] = {}
        self._stop_event = threading.Event()
        self._ui_after_id: str | None = None
        self._blink_after_id: str | None = None
        self._blink_phase = False
        self._slot_count = min(len(self.ADC_ADDRESSES), (self.num_boxes + 3) // 4)
        self._adc_modules: dict[int, object | None] = {
            slot: None for slot in range(self._slot_count)
        }
        self._adc_last_attempt = {
            slot: float("-inf") for slot in range(self._slot_count)
        }
        self._last_error_log = {slot: float("-inf") for slot in range(self._slot_count)}

        for index in range(self.num_boxes):
            self.create_analog_box(index, gas_types)

        self.log_writer_thread: threading.Thread | None = None
        self.adc_thread: threading.Thread | None = None
        if self.num_boxes:
            if self.logging_available:
                self.log_writer_thread = threading.Thread(
                    target=self._log_writer_worker,
                    name="gms-analog-log-writer",
                    daemon=True,
                )
                self.log_writer_thread.start()
            self.adc_thread = threading.Thread(
                target=self._adc_worker,
                name="gms-analog-reader",
                daemon=True,
            )
            self.adc_thread.start()
            self._ui_after_id = self.parent.after(100, self._process_sample_queue)
            self._blink_after_id = self.parent.after(500, self._blink_tick)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def create_analog_box(self, index: int, initial_gas_types: dict) -> None:
        box_frame = tk.Frame(
            self.parent,
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000000",
            highlightcolor="#000000",
        )
        inner = tk.Frame(box_frame)
        inner.pack(padx=1, pady=1)
        canvas = tk.Canvas(
            inner,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(1 * SCALE_FACTOR),
            highlightbackground="#000000",
            bg="white",
        )
        canvas.pack()
        canvas.create_rectangle(
            0,
            0,
            int(160 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR),
            fill="grey",
            outline="grey",
        )
        canvas.create_rectangle(
            0,
            int(200 * SCALE_FACTOR),
            int(160 * SCALE_FACTOR),
            int(310 * SCALE_FACTOR),
            fill="black",
            outline="black",
        )
        create_segment_display(canvas)

        gas_type = initial_gas_types.get(f"analog_box_{index}", "ORG")
        if gas_type not in self.GAS_FULL_SCALE:
            gas_type = "ORG"
        gas_var = tk.StringVar(value=gas_type)
        self.gas_types[f"analog_box_{index}"] = gas_var
        gas_text = canvas.create_text(
            *self.GAS_TYPE_POSITIONS[gas_type],
            text=gas_type,
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
        )

        badge_bg = canvas.create_rectangle(
            int(6 * SCALE_FACTOR),
            int(6 * SCALE_FACTOR),
            int(55 * SCALE_FACTOR),
            int(20 * SCALE_FACTOR),
            fill="#2b2b2b",
            outline="#444444",
            state="hidden",
        )
        badge_text = canvas.create_text(
            int(10 * SCALE_FACTOR),
            int(8 * SCALE_FACTOR),
            text="LOG 0",
            font=("Helvetica", int(8 * SCALE_FACTOR), "bold"),
            fill="#ffd966",
            anchor="nw",
            state="hidden",
        )

        circle_items = [
            canvas.create_oval(
                int(57 * SCALE_FACTOR),
                int(158 * SCALE_FACTOR),
                int(67 * SCALE_FACTOR),
                int(168 * SCALE_FACTOR),
            ),
            canvas.create_oval(
                int(93 * SCALE_FACTOR),
                int(158 * SCALE_FACTOR),
                int(103 * SCALE_FACTOR),
                int(168 * SCALE_FACTOR),
            ),
            canvas.create_oval(
                int(20 * SCALE_FACTOR),
                int(158 * SCALE_FACTOR),
                int(30 * SCALE_FACTOR),
                int(168 * SCALE_FACTOR),
            ),
            canvas.create_oval(
                int(131 * SCALE_FACTOR),
                int(158 * SCALE_FACTOR),
                int(141 * SCALE_FACTOR),
                int(168 * SCALE_FACTOR),
            ),
        ]
        for x, label in ((62, "AL1"), (98, "AL2"), (25, "PWR"), (136, "FUT")):
            canvas.create_text(
                int(x * SCALE_FACTOR),
                int(182 * SCALE_FACTOR),
                text=label,
                fill="#cccccc",
                font=("Helvetica", int(8 * SCALE_FACTOR)),
            )

        milliamp_var = tk.StringVar(value="PWR OFF")
        milliamp_text = canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(240 * SCALE_FACTOR),
            text=milliamp_var.get(),
            font=("Helvetica", int(10 * SCALE_FACTOR), "bold"),
            fill="#ff0000",
        )
        canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(270 * SCALE_FACTOR),
            text="GMS-1000",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
        )
        canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(295 * SCALE_FACTOR),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica", int(7 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
        )

        click_area = canvas.create_rectangle(
            int(10 * SCALE_FACTOR),
            int(25 * SCALE_FACTOR),
            int(140 * SCALE_FACTOR),
            int(90 * SCALE_FACTOR),
            outline="",
            fill="",
        )
        canvas.tag_bind(
            click_area, "<Button-1>", lambda _event, i=index: self.open_log_viewer(i)
        )
        canvas.segment_canvas.bind(
            "<Button-1>", lambda _event, i=index: self.open_log_viewer(i)
        )

        state = {
            "current_ma": 0.0,
            "raw_engineering": 0.0,
            "display_value": 0.0,
            "pwr_on": False,
            "alarm1_on": False,
            "alarm2_on": False,
            "fault": False,
            "fault_reason": "",
            "healthy_samples": 0,
            "gas_type_text_id": gas_text,
            "milliamp_var": milliamp_var,
            "milliamp_text_id": milliamp_text,
            "log_badge_bg": badge_bg,
            "log_badge_text": badge_text,
            "last_logged_display": None,
            "last_logged_alarm1": None,
            "last_logged_alarm2": None,
            "last_logged_pwr": None,
            "last_logged_fault": None,
            "last_value_log_time": 0.0,
        }
        self.box_frames.append(box_frame)
        self.box_data.append((canvas, circle_items))
        self.box_states.append(state)
        gas_var.trace_add(
            "write",
            lambda *_args, variable=gas_var, i=index: self.update_full_scale(
                variable, i
            ),
        )
        self._render_box(index)
        self.update_log_badge(index)

    # ------------------------------------------------------------------
    # ADC worker
    # ------------------------------------------------------------------
    def _open_adc(self, slot: int) -> object | None:
        if Adafruit_ADS1x15 is None:
            return None
        now = time.monotonic()
        if now - self._adc_last_attempt[slot] < self.ADC_RETRY_SEC:
            return self._adc_modules[slot]
        self._adc_last_attempt[slot] = now
        address = self.ADC_ADDRESSES[slot]
        try:
            adc = Adafruit_ADS1x15.ADS1115(address=address)
            adc.read_adc(0, gain=GAIN)
            self._adc_modules[slot] = adc
            print(f"ADS1115 0x{address:02X} 연결 성공 (slot={slot})")
            return adc
        except Exception as exc:
            self._adc_modules[slot] = None
            self._queue_adc_error(slot, f"ADC 0x{address:02X} 연결 실패: {exc}")
            return None

    def _queue_adc_error(self, slot: int, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_log[slot] < self.ADC_RETRY_SEC:
            return
        self._last_error_log[slot] = now
        self._put_sample(("error", slot, message))

    def _put_sample(self, item: tuple[str, int, object]) -> None:
        kind, index, payload = item
        if kind == "sample":
            with self._sample_lock:
                self._pending_adc_samples[index] = payload
            return
        try:
            self.sample_queue.put_nowait(item)
        except queue.Full:
            # Error events are already rate-limited per ADC. Keep the newest
            # bounded set if a modal UI prevented timely processing.
            try:
                self.sample_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.sample_queue.put_nowait(item)
            except queue.Full:
                pass

    def _adc_worker(self) -> None:
        if Adafruit_ADS1x15 is None:
            for slot in range(self._slot_count):
                self._queue_adc_error(
                    slot, "Adafruit_ADS1x15 라이브러리가 설치되지 않았습니다."
                )
        while not self._stop_event.is_set():
            for slot, _address in enumerate(self.ADC_ADDRESSES[: self._slot_count]):
                if self._stop_event.is_set():
                    break
                adc = self._adc_modules[slot] or self._open_adc(slot)
                if adc is None:
                    continue
                try:
                    for channel in range(4):
                        box_index = slot * 4 + channel
                        if box_index >= self.num_boxes:
                            break
                        raw = adc.read_adc(channel, gain=GAIN)
                        voltage = float(raw) * 6.144 / 32767.0
                        milliamp = (voltage / 250.0) * 1000.0
                        history = self.adc_values[box_index]
                        filtered = (
                            milliamp
                            if not history
                            else 0.7 * milliamp + 0.3 * history[-1]
                        )
                        history.append(filtered)
                        self._put_sample(
                            (
                                "sample",
                                box_index,
                                {"filtered": filtered, "raw": milliamp},
                            )
                        )
                except Exception as exc:
                    self._adc_modules[slot] = None
                    self._adc_last_attempt[slot] = time.monotonic()
                    self._queue_adc_error(
                        slot, f"ADC 0x{self.ADC_ADDRESSES[slot]:02X} 읽기 오류: {exc}"
                    )
            self._stop_event.wait(0.1)

    # ------------------------------------------------------------------
    # Main-thread update and rendering
    # ------------------------------------------------------------------
    def _process_sample_queue(self) -> None:
        self._ui_after_id = None
        if self._stop_event.is_set():
            return
        latest: dict[int, float] = {}
        latest_raw: dict[int, float] = {}
        errors: list[tuple[int, str]] = []
        with self._sample_lock:
            pending_samples = self._pending_adc_samples
            self._pending_adc_samples = {}
        for index, payload in pending_samples.items():
            try:
                if isinstance(payload, dict):
                    latest[index] = float(payload["filtered"])
                    latest_raw[index] = float(payload["raw"])
                else:
                    latest[index] = float(payload)
                    latest_raw[index] = float(payload)
            except (KeyError, TypeError, ValueError) as exc:
                print(f"Analog sample 무시: {exc}")
        try:
            while True:
                kind, index, payload = self.sample_queue.get_nowait()
                if kind == "sample":
                    try:
                        if isinstance(payload, dict):
                            latest[index] = float(payload["filtered"])
                            latest_raw[index] = float(payload["raw"])
                        else:
                            latest[index] = float(payload)
                            latest_raw[index] = float(payload)
                    except (KeyError, TypeError, ValueError) as exc:
                        print(f"Analog sample 무시: {exc}")
                else:
                    errors.append((index, str(payload)))
        except queue.Empty:
            pass
        try:
            for box_index, milliamp in latest.items():
                try:
                    self._apply_sample(
                        box_index,
                        milliamp,
                        signal_milliamp=latest_raw.get(box_index, milliamp),
                    )
                except (ValueError, tk.TclError) as exc:
                    print(f"Analog UI sample 적용 오류 (box={box_index}): {exc}")
            for slot, message in errors:
                try:
                    self._apply_adc_error(slot, message)
                except (ValueError, tk.TclError) as exc:
                    print(f"Analog UI 오류 적용 실패 (slot={slot}): {exc}")
        finally:
            if not self._stop_event.is_set():
                try:
                    self._ui_after_id = self.parent.after(
                        100, self._process_sample_queue
                    )
                except tk.TclError:
                    self._stop_event.set()

    def _apply_sample(
        self,
        box_index: int,
        milliamp: float,
        *,
        signal_milliamp: float | None = None,
    ) -> None:
        if not 0 <= box_index < self.num_boxes:
            return
        raw_signal = milliamp if signal_milliamp is None else signal_milliamp
        if not math.isfinite(milliamp) or not math.isfinite(raw_signal):
            self._apply_box_fault(box_index, "ADC 값이 유한수가 아닙니다.")
            return
        gas_type = self.gas_types[f"analog_box_{box_index}"].get()
        full_scale = self.GAS_FULL_SCALE.get(gas_type, self.GAS_FULL_SCALE["ORG"])
        raw_engineering = round(scale_4_20ma(milliamp, full_scale), 6)
        display_value = display_engineering_value(gas_type, raw_engineering)
        pwr_on = raw_signal >= 1.5
        thresholds = self.ALARM_LEVELS.get(gas_type, self.ALARM_LEVELS["ORG"])
        alarm1 = pwr_on and raw_engineering >= thresholds["AL1"]
        alarm2 = pwr_on and raw_engineering >= thresholds["AL2"]
        if alarm2:
            alarm1 = True

        state = self.box_states[box_index]
        signal_fault = analog_signal_fault(
            raw_signal,
            low=self.SIGNAL_FAULT_LOW_MA,
            high=self.SIGNAL_FAULT_HIGH_MA,
        )
        if signal_fault:
            healthy_samples = 0
            fault = True
            fault_reason = f"4-20mA 범위 오류: {raw_signal:.2f}mA"
        elif state.get("fault"):
            healthy_samples = int(state.get("healthy_samples", 0)) + 1
            fault = healthy_samples < self.FAULT_CLEAR_SAMPLES
            fault_reason = str(state.get("fault_reason") or "") if fault else ""
        else:
            healthy_samples = self.FAULT_CLEAR_SAMPLES
            fault = False
            fault_reason = ""
        if fault:
            # Until the configured healthy-sample count is reached, an ADC or
            # current-loop fault must not erase a previously active gas alarm.
            alarm1 = alarm1 or bool(state.get("alarm1_on"))
            alarm2 = alarm2 or bool(state.get("alarm2_on"))
            pwr_on = pwr_on or bool(state.get("pwr_on"))
            if alarm2:
                alarm1 = True
        state.update(
            {
                "current_ma": milliamp,
                "raw_engineering": raw_engineering,
                "display_value": display_value,
                "pwr_on": pwr_on,
                "alarm1_on": alarm1,
                "alarm2_on": alarm2,
                "fault": fault,
                "fault_reason": fault_reason,
                "healthy_samples": healthy_samples,
            }
        )
        self._render_box(box_index)
        self.maybe_log_event(
            box_index,
            milliamp,
            raw_engineering,
            gas_type,
            alarm1,
            alarm2,
            pwr_on,
            fault,
        )

    def _apply_box_fault(self, box_index: int, message: str) -> None:
        state = self.box_states[box_index]
        was_fault = bool(state.get("fault"))
        alarm1 = bool(state.get("alarm1_on"))
        alarm2 = bool(state.get("alarm2_on"))
        pwr_on = bool(state.get("pwr_on"))
        state.update(
            {
                "current_ma": 0.0,
                "raw_engineering": 0.0,
                "display_value": 0.0,
                "pwr_on": pwr_on,
                "alarm1_on": alarm1,
                "alarm2_on": alarm2,
                "fault": True,
                "fault_reason": message,
                "healthy_samples": 0,
            }
        )
        self._render_box(box_index)
        if not was_fault:
            gas_type = self.gas_types[f"analog_box_{box_index}"].get()
            self.maybe_log_event(
                box_index,
                0.0,
                0.0,
                gas_type,
                alarm1,
                alarm2,
                pwr_on,
                True,
                event=message,
                force=True,
            )

    def _apply_adc_error(self, slot: int, message: str) -> None:
        print(message)
        start = slot * 4
        for box_index in range(start, min(start + 4, self.num_boxes)):
            self.adc_values[box_index].clear()
            self._apply_box_fault(box_index, message)

    def _notify_alarm(self, active: bool, box_id: str, fut: bool = False) -> None:
        try:
            self.alarm_callback(active, box_id, fut)
        except TypeError:
            self.alarm_callback(active, box_id)

    def _render_box(self, box_index: int) -> None:
        state = self.box_states[box_index]
        canvas, circles = self.box_data[box_index]
        pwr = bool(state["pwr_on"])
        alarm1 = bool(state["alarm1_on"])
        alarm2 = bool(state["alarm2_on"])
        fault = bool(state.get("fault"))

        if fault:
            self._render_segments(canvas, "Err")
            milliamp_text = (
                f"FUT {float(state['current_ma']):.1f} mA"
                if state.get("current_ma")
                else "ADC FUT"
            )
            milliamp_color = "#ffd400"
        elif pwr:
            gas_type = self.gas_types[f"analog_box_{box_index}"].get()
            display = format_engineering_value(gas_type, state["raw_engineering"])
            self._render_segments(canvas, display)
            milliamp_text = f"{state['current_ma']:.1f} mA"
            milliamp_color = "#00ff00"
        else:
            self._render_segments(canvas, "    ")
            milliamp_text = "PWR OFF"
            milliamp_color = "#ff0000"
        state["milliamp_var"].set(milliamp_text)
        canvas.itemconfig(
            state["milliamp_text_id"], text=milliamp_text, fill=milliamp_color
        )

        blink = self._blink_phase
        al1_visible = alarm1 and (not alarm2) and blink
        al2_visible = alarm2 and blink
        # AL1 stays on while AL2 is active.
        al1_color = "red" if (alarm2 or al1_visible) else "#fdc8c8"
        al2_color = "red" if al2_visible else "#fdc8c8"
        pwr_color = "green" if pwr else "#e0fbba"
        fut_color = "yellow" if fault and blink else "#fcf1bf"
        for item, color in zip(circles, (al1_color, al2_color, pwr_color, fut_color)):
            canvas.itemconfig(item, fill=color, outline=color)

        if (alarm1 or alarm2) and blink:
            border_color = "#ff0000"
        elif fault and blink:
            border_color = "#ffd400"
        else:
            border_color = "#000000"
        self.box_frames[box_index].config(highlightbackground=border_color)
        self._notify_alarm(alarm1 or alarm2, f"analog_{box_index}", fault)

    def _render_segments(self, canvas: tk.Canvas, value: str) -> None:
        tokens: list[list[object]] = []
        for char in str(value).strip():
            if char == "." and tokens:
                tokens[-1][1] = True
            else:
                tokens.append([char, False])
        tokens = ([[" ", False]] * max(0, 4 - len(tokens)) + tokens)[-4:]
        for position, (digit, dot_on) in enumerate(tokens):
            pattern = SEGMENTS.get(str(digit), SEGMENTS[" "])
            for segment_index, enabled in enumerate(pattern[:7]):
                tag = f"segment_{position}_{chr(97 + segment_index)}"
                canvas.segment_canvas.itemconfig(
                    tag, fill=SEGMENT_ON if enabled == "1" else SEGMENT_OFF
                )
            dot_color = SEGMENT_ON if dot_on else SEGMENT_OFF
            canvas.segment_canvas.itemconfig(
                f"segment_{position}_dot", fill=dot_color, outline=dot_color
            )

    def _blink_tick(self) -> None:
        self._blink_after_id = None
        if self._stop_event.is_set():
            return
        self._blink_phase = not self._blink_phase
        for index in range(self.num_boxes):
            try:
                self._render_box(index)
            except tk.TclError as exc:
                print(f"Analog blink 렌더 오류 (box={index}): {exc}")
        try:
            self._blink_after_id = self.parent.after(500, self._blink_tick)
        except tk.TclError:
            self._stop_event.set()

    # ------------------------------------------------------------------
    # Gas type and logs
    # ------------------------------------------------------------------
    def update_full_scale(self, gas_type_var: tk.StringVar, box_index: int) -> None:
        gas_type = gas_type_var.get()
        if gas_type not in self.GAS_FULL_SCALE:
            gas_type = "ORG"
            gas_type_var.set(gas_type)
            return
        state = self.box_states[box_index]
        canvas = self.box_data[box_index][0]
        canvas.coords(state["gas_type_text_id"], *self.GAS_TYPE_POSITIONS[gas_type])
        canvas.itemconfig(state["gas_type_text_id"], text=gas_type)
        self._apply_sample(box_index, float(state["current_ma"]))
        self.maybe_log_event(
            box_index,
            float(state["current_ma"]),
            float(state["raw_engineering"]),
            gas_type,
            bool(state["alarm1_on"]),
            bool(state["alarm2_on"]),
            bool(state["pwr_on"]),
            bool(state.get("fault")),
            event=f"GAS_TYPE_CHANGED:{gas_type}",
            force=True,
        )

    def maybe_log_event(
        self,
        box_index: int,
        raw_ma: float,
        raw_engineering: float,
        gas_type: str,
        alarm1: bool,
        alarm2: bool,
        pwr_on: bool,
        fault: bool = False,
        event: str = "",
        force: bool = False,
    ) -> None:
        state = self.box_states[box_index]
        display_value = display_engineering_value(gas_type, raw_engineering)
        resolution = 0.1 if gas_type == "HMDS" else 1.0
        previous = state.get("last_logged_display")
        state_changed = (
            alarm1 != state.get("last_logged_alarm1")
            or alarm2 != state.get("last_logged_alarm2")
            or pwr_on != state.get("last_logged_pwr")
            or fault != state.get("last_logged_fault")
        )
        value_changed = (
            previous is None or abs(display_value - float(previous)) >= resolution
        )
        now = time.monotonic()
        timed_value_change = value_changed and (
            now - float(state.get("last_value_log_time", 0.0))
            >= self.VALUE_LOG_INTERVAL_SEC
        )
        if not (force or event or state_changed or timed_value_change):
            return

        if not event:
            reasons = []
            if previous is None:
                reasons.append("INIT")
            previous_alarm1 = state.get("last_logged_alarm1")
            previous_alarm2 = state.get("last_logged_alarm2")
            previous_pwr = state.get("last_logged_pwr")
            previous_fault = state.get("last_logged_fault")
            if previous_alarm1 is not None and alarm1 != previous_alarm1:
                reasons.append("AL1_ON" if alarm1 else "AL1_OFF")
            if previous_alarm2 is not None and alarm2 != previous_alarm2:
                reasons.append("AL2_ON" if alarm2 else "AL2_OFF")
            if previous_pwr is None or pwr_on != previous_pwr:
                reasons.append("PWR_ON" if pwr_on else "PWR_OFF")
            if previous_fault is None or fault != previous_fault:
                reasons.append("FUT_ON" if fault else "FUT_OFF")
            if timed_value_change:
                reasons.append("VALUE_CHANGED")
            event = "|".join(reasons) or "VALUE_CHANGED"

        state["last_logged_display"] = display_value
        state["last_logged_alarm1"] = alarm1
        state["last_logged_alarm2"] = alarm2
        state["last_logged_pwr"] = pwr_on
        state["last_logged_fault"] = fault
        state["last_value_log_time"] = now
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        numeric_display = (
            round(display_value, 1) if gas_type == "HMDS" else int(round(display_value))
        )
        entry = (timestamp, numeric_display, alarm1, alarm2, event)
        logs = self.box_logs[box_index]
        logs.append(entry)
        self.log_sequences[box_index] += 1
        if len(logs) > self.LOG_MAX_ENTRIES:
            del logs[: len(logs) - self.LOG_MAX_ENTRIES]
        if self.logging_available:
            item = (
                "row",
                box_index,
                [
                    timestamp,
                    box_index + 1,
                    gas_type,
                    f"{raw_ma:.4f}",
                    numeric_display,
                    f"{raw_engineering:.3f}",
                    int(alarm1),
                    int(alarm2),
                    int(pwr_on),
                    int(fault),
                    event,
                ],
            )
            critical = event != "VALUE_CHANGED"
            self._enqueue_log(item, critical=critical)
        self.update_log_badge(box_index)

    @staticmethod
    def _is_routine_log(item: tuple[str, int, list]) -> bool:
        kind, _box_index, row = item
        return kind == "row" and bool(row) and str(row[-1]) == "VALUE_CHANGED"

    @staticmethod
    def _critical_log_key(item: tuple[str, int, list]) -> tuple[str, int, str]:
        kind, box_index, row = item
        event = str(row[-1]) if kind == "row" and row else ""
        return kind, box_index, event

    def _set_logging_fault(self, active: bool) -> None:
        active = bool(active)
        if self._logging_fault_active == active:
            return
        self._logging_fault_active = active
        callback = getattr(self, "alarm_callback", None)
        if callable(callback):
            self._notify_alarm(False, "analog_logging", active)

    def _publish_logging_fault(self, active: bool) -> None:
        if threading.current_thread() is threading.main_thread():
            self._set_logging_fault(active)
        else:
            utils.dispatch_ui(self._set_logging_fault, active)

    def _record_log_loss(self, *, critical: bool) -> int:
        with self._log_condition:
            self.log_dropped_count += 1
            if critical:
                self.log_dropped_critical_count += 1
                count = self.log_dropped_critical_count
            else:
                count = self.log_dropped_count
        if critical:
            self._publish_logging_fault(True)
            if count == 1 or count & (count - 1) == 0:
                print(f"Analog 중요 로그 일부를 저장하지 못했습니다 (누적 {count}건).")
        return count

    def _enqueue_log(self, item: tuple[str, int, list], *, critical: bool) -> bool:
        """Keep the SD writer backlog finite while retaining the latest state."""

        dropped_critical = False
        dropped_routine = False
        with self._log_condition:
            limit = max(1, int(self.LOG_QUEUE_MAX))
            if len(self.log_queue) >= limit:
                if not critical:
                    dropped_routine = True
                else:
                    routine_index = next(
                        (
                            index
                            for index, queued in enumerate(self.log_queue)
                            if self._is_routine_log(queued)
                        ),
                        None,
                    )
                    if routine_index is not None:
                        del self.log_queue[routine_index]
                        dropped_routine = True
                    else:
                        # Every critical row contains a full box-state snapshot.
                        # Prefer replacing an older matching event, then any older
                        # snapshot for the same box. If neither exists, evict the
                        # oldest critical row. This guarantees the Pi's memory use
                        # stays bounded even if SD I/O blocks indefinitely.
                        key = self._critical_log_key(item)
                        replacement_index = next(
                            (
                                index
                                for index, queued in enumerate(self.log_queue)
                                if self._critical_log_key(queued) == key
                            ),
                            None,
                        )
                        if replacement_index is None:
                            replacement_index = next(
                                (
                                    index
                                    for index, queued in enumerate(self.log_queue)
                                    if queued[0] == item[0] and queued[1] == item[1]
                                ),
                                0,
                            )
                        del self.log_queue[replacement_index]
                        dropped_critical = True
            if not dropped_routine or critical:
                self.log_queue.append(item)
                self._log_condition.notify()

        if dropped_routine:
            self._record_log_loss(critical=False)
        if dropped_critical:
            self._record_log_loss(critical=True)
        return not dropped_routine or critical

    def _dequeue_log(self, timeout: float = 0.5) -> tuple[str, int, list] | None:
        with self._log_condition:
            if not self.log_queue and not self._stop_event.is_set():
                self._log_condition.wait(timeout=timeout)
            return self.log_queue.popleft() if self.log_queue else None

    def _has_pending_logs(self) -> bool:
        with self._log_condition:
            return bool(self.log_queue)

    def _log_writer_worker(self) -> None:
        handles: dict[int, dict] = {}
        last_prune_time = 0.0
        last_write_error_notice = 0.0

        def close_handle(box_index: int) -> None:
            info = handles.pop(box_index, None)
            if not info:
                return
            close_error: OSError | None = None
            try:
                info["handle"].flush()
            except OSError as exc:
                close_error = exc
            try:
                info["handle"].close()
            except OSError as exc:
                close_error = close_error or exc
            if close_error is not None:
                print(f"Analog 로그 닫기 오류: {close_error}")
                self._record_log_loss(critical=False)
                self._publish_logging_fault(True)

        def prune_old_logs(*, force: bool = False) -> None:
            nonlocal last_prune_time
            now = time.time()
            if not force and now - last_prune_time < 3600:
                return
            last_prune_time = now
            cutoff = now - self.LOG_RETENTION_DAYS * 86400
            try:
                for path in self.LOG_DIR.glob("analog_box_*-*.csv"):
                    try:
                        if path.stat().st_mtime < cutoff:
                            path.unlink()
                    except OSError as exc:
                        print(f"오래된 Analog 로그 정리 실패 ({path.name}): {exc}")
            except OSError as exc:
                print(f"Analog 로그 보존 정책 적용 실패: {exc}")

        prune_old_logs(force=True)

        try:
            while not self._stop_event.is_set() or self._has_pending_logs():
                prune_old_logs()
                item = self._dequeue_log(timeout=0.5)
                if item is None:
                    continue
                kind, box_index, row = item
                if kind == "clear":
                    close_handle(box_index)
                    paths = list(self.LOG_DIR.glob(f"analog_box_{box_index + 1}-*.csv"))
                    paths.append(self.LOG_DIR / f"analog_box_{box_index + 1}.csv")
                    for path in paths:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError as exc:
                            print(f"Analog 로그 삭제 실패 ({path.name}): {exc}")
                            self._record_log_loss(critical=True)
                    continue
                if kind != "row" or not row:
                    continue

                critical = str(row[-1]) != "VALUE_CHANGED"
                date_key = str(row[0])[:10].replace("-", "")
                if len(date_key) != 8 or not date_key.isdigit():
                    date_key = time.strftime("%Y%m%d")
                info = handles.get(box_index)
                if info and info["date"] != date_key:
                    close_handle(box_index)
                    info = None
                path = self.LOG_DIR / f"analog_box_{box_index + 1}-{date_key}.csv"
                try:
                    if info is None:
                        new_file = not path.exists() or path.stat().st_size == 0
                        handle = path.open(
                            "a",
                            newline="",
                            encoding="utf-8-sig",
                            buffering=65536,
                        )
                        try:
                            os.chmod(path, 0o600)
                        except OSError:
                            pass
                        info = {
                            "date": date_key,
                            "handle": handle,
                            "writer": csv.writer(handle),
                            "last_flush": time.monotonic(),
                        }
                        handles[box_index] = info
                        if new_file:
                            info["writer"].writerow(
                                [
                                    "timestamp",
                                    "box_index",
                                    "gas_type",
                                    "raw_mA",
                                    "display_value",
                                    "raw_engineering_value",
                                    "alarm1",
                                    "alarm2",
                                    "pwr_on",
                                    "fault",
                                    "event",
                                ]
                            )
                    info["writer"].writerow(row)
                    now = time.monotonic()
                    durable = False
                    if (
                        critical
                        or now - info["last_flush"] >= self.LOG_FLUSH_INTERVAL_SEC
                    ):
                        info["handle"].flush()
                        if critical:
                            os.fsync(info["handle"].fileno())
                        info["last_flush"] = now
                        durable = True
                    if durable:
                        with self._log_condition:
                            backlog_recovered = len(self.log_queue) <= max(
                                1, int(self.LOG_QUEUE_MAX) // 2
                            )
                        if backlog_recovered:
                            self._publish_logging_fault(False)
                except OSError as exc:
                    now = time.monotonic()
                    if now - last_write_error_notice >= 60.0:
                        print(f"Analog 로그 저장 오류: {exc}")
                        last_write_error_notice = now
                    self._record_log_loss(critical=critical)
                    # An actual storage failure is a subsystem fault even when
                    # the lost row happened to be routine telemetry.
                    self._publish_logging_fault(True)
                    close_handle(box_index)
        finally:
            for box_index in list(handles):
                close_handle(box_index)

    def update_log_badge(self, box_index: int) -> None:
        if not 0 <= box_index < self.num_boxes:
            return
        state = self.box_states[box_index]
        canvas = self.box_data[box_index][0]
        unread = max(
            0, self.log_sequences[box_index] - self.last_viewed_sequence[box_index]
        )
        bg = state["log_badge_bg"]
        text = state["log_badge_text"]
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

    def open_log_viewer(self, box_index: int) -> None:
        existing = self.log_viewers[box_index]
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        self.last_viewed_sequence[box_index] = self.log_sequences[box_index]
        self.update_log_badge(box_index)

        def clear() -> None:
            self.box_logs[box_index].clear()
            self.last_viewed_sequence[box_index] = self.log_sequences[box_index]
            if self.logging_available:
                self._enqueue_log(("clear", box_index, []), critical=True)
            self.update_log_badge(box_index)

        def closed() -> None:
            self.log_viewers[box_index] = None

        self.log_viewers[box_index] = LogViewer(
            self.parent,
            box_index=box_index,
            ip=f"ANALOG BOX {box_index + 1}",
            get_logs_callable=lambda: self.box_logs[box_index],
            on_clear_callable=clear,
            on_close_callable=closed,
        )

    def stop(self) -> None:
        self._stop_event.set()
        with self._sample_lock:
            self._pending_adc_samples.clear()
        with self._log_condition:
            self._log_condition.notify_all()
        for after_id in (self._ui_after_id, self._blink_after_id):
            if after_id:
                try:
                    self.parent.after_cancel(after_id)
                except tk.TclError:
                    pass
        self._ui_after_id = None
        self._blink_after_id = None
        for viewer in self.log_viewers:
            if viewer is not None:
                try:
                    viewer.close()
                except Exception:
                    pass
        for thread in (self.adc_thread, self.log_writer_thread):
            if (
                thread is not None
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                thread.join(timeout=3.0)
