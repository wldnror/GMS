# analog_ui.py

import os
import csv
import time
import threading
from collections import deque
from tkinter import Frame, Canvas, StringVar
import Adafruit_ADS1x15
import queue
import asyncio
import tkinter as tk

from common import SEGMENTS, create_segment_display, SCALE
from log_viewer import LogViewer

# 전역 변수로 설정
GAIN = 2 / 3
SCALE_FACTOR = 1.65  # 20% 키우기


class AnalogUI:
    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000.0,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG": (int(115 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "ARF-T": (int(107 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "HMDS": (int(110 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "HC-100": (int(104 * SCALE_FACTOR), int(95 * SCALE_FACTOR))
    }

    ALARM_LEVELS = {
        "ORG": {"AL1": 9500, "AL2": 9999},
        "ARF-T": {"AL1": 2000, "AL2": 4000},
        "HMDS": {"AL1": 2640.0, "AL2": 3000.0},
        "HC-100": {"AL1": 1500, "AL2": 3000}
    }

    LOG_DIR = "analog_logs"
    LOG_MAX_ENTRIES = 1000

    def __init__(self, parent, num_boxes, gas_types, alarm_callback):
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.gas_types = {}
        self.num_boxes = num_boxes
        self.box_states = []
        self.box_frames = []
        self.box_data = []

        # 필터링용 최근 값
        self.adc_values = [deque(maxlen=3) for _ in range(num_boxes)]

        # 로그 관련
        self._init_log_dir()
        self.box_logs = [[] for _ in range(num_boxes)]
        self.last_viewed_log_len = [0] * num_boxes
        self.log_viewers = [None] * num_boxes
        self.log_queue = queue.Queue()
        self.log_writer_thread = threading.Thread(target=self._log_writer_worker, daemon=True)
        self.log_writer_thread.start()

        for i in range(num_boxes):
            self.create_analog_box(i, gas_types)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        self.adc_queue = queue.Queue()
        self.start_adc_thread()
        self.schedule_ui_update()

    # -------------------------------------------------------------------------
    # 로그 기본
    # -------------------------------------------------------------------------
    def _init_log_dir(self):
        os.makedirs(self.LOG_DIR, exist_ok=True)

    def _get_log_file_path(self, box_index: int) -> str:
        return os.path.join(self.LOG_DIR, f"analog_box_{box_index + 1}.csv")

    def _ensure_log_header(self, file_path: str):
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp",
                    "box_index",
                    "gas_type",
                    "raw_mA",
                    "display_value",
                    "full_scale_value",
                    "alarm1",
                    "alarm2",
                    "pwr_on",
                    "event"
                ])

    def _queue_log_row(self, box_index: int, row: list):
        self.log_queue.put((box_index, row))

    def _log_writer_worker(self):
        while True:
            try:
                box_index, row = self.log_queue.get(timeout=1)
                file_path = self._get_log_file_path(box_index)
                self._ensure_log_header(file_path)

                with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(row)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[Analog Log] CSV write error: {e}")

    def _append_memory_log(self, box_index: int, entry):
        logs = self.box_logs[box_index]
        logs.append(entry)
        if len(logs) > self.LOG_MAX_ENTRIES:
            del logs[0]

    def _display_resolution_for_gas(self, gas_type: str) -> float:
        return 0.1 if gas_type == "HMDS" else 1.0

    def _make_log_tuple(self, ts, value_str, alarm1, alarm2, extra_event):
        """
        ModbusUI의 로그 뷰어가 tuple 형태를 기대할 수 있으므로 비슷하게 맞춤.
        (timestamp, value, alarm1, alarm2, extra)
        """
        return (ts, value_str, alarm1, alarm2, extra_event)

    def maybe_log_event(self, box_index: int, raw_mA: float, full_scale_value: float,
                        gas_type: str, alarm1: bool, alarm2: bool, pwr_on: bool, event: str = ""):
        state = self.box_states[box_index]

        resolution = self._display_resolution_for_gas(gas_type)
        if resolution == 0.1:
            normalized_display = round(float(full_scale_value), 1)
            display_value_str = f"{normalized_display:.1f}"
        else:
            normalized_display = int(round(float(full_scale_value)))
            display_value_str = str(normalized_display)

        prev_display = state.get("last_logged_display")
        prev_alarm1 = state.get("last_logged_alarm1")
        prev_alarm2 = state.get("last_logged_alarm2")
        prev_pwr = state.get("last_logged_pwr")

        changed = (
            prev_display is None
            or abs(float(normalized_display) - float(prev_display)) >= resolution
            or alarm1 != prev_alarm1
            or alarm2 != prev_alarm2
            or pwr_on != prev_pwr
            or bool(event)
        )

        if not changed:
            return

        # 이벤트명 자동 구성
        if not event:
            reasons = []
            if prev_display is None:
                reasons.append("INIT")
            else:
                if alarm1 != prev_alarm1:
                    reasons.append("AL1_ON" if alarm1 else "AL1_OFF")
                if alarm2 != prev_alarm2:
                    reasons.append("AL2_ON" if alarm2 else "AL2_OFF")
                if pwr_on != prev_pwr:
                    reasons.append("PWR_ON" if pwr_on else "PWR_OFF")
                if abs(float(normalized_display) - float(prev_display if prev_display is not None else normalized_display)) >= resolution:
                    reasons.append("VALUE_CHANGED")

            event = "|".join(reasons) if reasons else "VALUE_CHANGED"

        state["last_logged_display"] = normalized_display
        state["last_logged_alarm1"] = alarm1
        state["last_logged_alarm2"] = alarm2
        state["last_logged_pwr"] = pwr_on

        ts = time.strftime("%Y-%m-%d %H:%M:%S")

        # 메모리 로그 (LogViewer용)
        mem_entry = self._make_log_tuple(
            ts=ts,
            value_str=f"{display_value_str} ({raw_mA:.3f}mA, {gas_type}, PWR={'ON' if pwr_on else 'OFF'})",
            alarm1=alarm1,
            alarm2=alarm2,
            extra_event=event
        )
        self._append_memory_log(box_index, mem_entry)

        # CSV 저장
        csv_row = [
            ts,
            box_index + 1,
            gas_type,
            f"{float(raw_mA):.4f}",
            display_value_str,
            f"{float(full_scale_value):.3f}",
            int(bool(alarm1)),
            int(bool(alarm2)),
            int(bool(pwr_on)),
            event
        ]
        self._queue_log_row(box_index, csv_row)

        print(
            f"[Analog Log] Box {box_index + 1} | {gas_type} | "
            f"{raw_mA:.4f} mA | value={display_value_str} | "
            f"AL1={alarm1} AL2={alarm2} PWR={pwr_on} | {event}"
        )

        self.parent.after(0, lambda idx=box_index: self.update_log_badge(idx))

    def update_log_badge(self, box_index: int):
        st = self.box_states[box_index]
        box_canvas = self.box_data[box_index][0]

        bg_id = st.get("log_badge_bg")
        tx_id = st.get("log_badge_text")
        if bg_id is None or tx_id is None:
            return

        total = len(self.box_logs[box_index])
        unread = max(0, total - int(self.last_viewed_log_len[box_index]))

        if unread <= 0:
            box_canvas.itemconfig(bg_id, state="hidden")
            box_canvas.itemconfig(tx_id, state="hidden")
            return

        label = f"LOG {unread}"
        box_canvas.itemconfig(tx_id, text=label, state="normal")

        try:
            box_canvas.update_idletasks()
            bbox = box_canvas.bbox(tx_id)
            if bbox:
                x1, y1, x2, y2 = bbox
                pad_x, pad_y = int(4 * SCALE_FACTOR), int(2 * SCALE_FACTOR)
                box_canvas.coords(bg_id, x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y)
        except Exception:
            pass

        box_canvas.itemconfig(bg_id, state="normal")

    def open_log_viewer(self, box_index: int):
        existing = self.log_viewers[box_index]
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            return

        box_name = f"ANALOG BOX {box_index + 1}"

        def _get_logs():
            return self.box_logs[box_index]

        def _clear_logs():
            self.box_logs[box_index].clear()
            self.last_viewed_log_len[box_index] = 0
            self.update_log_badge(box_index)

        win = LogViewer(
            self.parent,
            box_index=box_index,
            ip=box_name,
            get_logs_callable=_get_logs,
            on_clear_callable=_clear_logs,
        )
        self.log_viewers[box_index] = win

        def _on_close():
            self.log_viewers[box_index] = None
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _on_close)

        self.last_viewed_log_len[box_index] = len(self.box_logs[box_index])
        self.update_log_badge(box_index)

    # -------------------------------------------------------------------------
    # UI 생성
    # -------------------------------------------------------------------------
    def create_analog_box(self, index, initial_gas_types):
        box_frame = Frame(self.parent, highlightthickness=int(7 * SCALE_FACTOR))

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=int(1), pady=int(1))

        box_canvas = Canvas(
            inner_frame,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000000",
            highlightcolor="#000000",
            bg='white'
        )
        box_canvas.pack()

        box_canvas.create_rectangle(
            0, 0,
            int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR),
            fill='grey', outline='grey', tags='border'
        )
        box_canvas.create_rectangle(
            0, int(200 * SCALE_FACTOR),
            int(160 * SCALE_FACTOR), int(310 * SCALE_FACTOR),
            fill='black', outline='grey', tags='border'
        )

        create_segment_display(box_canvas)

        # 세그먼트 클릭 영역
        seg_x1, seg_y1 = int(10 * SCALE_FACTOR), int(25 * SCALE_FACTOR)
        seg_x2, seg_y2 = int((150 - 10) * SCALE_FACTOR), int(90 * SCALE_FACTOR)
        box_canvas.create_rectangle(
            seg_x1, seg_y1, seg_x2, seg_y2,
            outline="",
            fill="",
            tags="segment_click_area"
        )

        def _on_segment_click(event, idx=index):
            self.open_log_viewer(idx)

        box_canvas.tag_bind("segment_click_area", "<Button-1>", _on_segment_click)
        if hasattr(box_canvas, "segment_canvas"):
            box_canvas.segment_canvas.bind("<Button-1>", _on_segment_click)

        gas_type_value = initial_gas_types.get(f"analog_box_{index}", "ORG")
        gas_type_var = StringVar(value=gas_type_value)
        gas_type_var.trace_add("write", lambda *args, var=gas_type_var, idx=index: self.update_full_scale(var, idx))
        self.gas_types[f"analog_box_{index}"] = gas_type_var

        gas_type_text_id = box_canvas.create_text(
            *self.GAS_TYPE_POSITIONS[gas_type_var.get()],
            text=gas_type_var.get(),
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )

        badge_bg = box_canvas.create_rectangle(
            int(6 * SCALE_FACTOR), int(6 * SCALE_FACTOR),
            int(55 * SCALE_FACTOR), int(20 * SCALE_FACTOR),
            fill="#2b2b2b",
            outline="#444444",
            state="hidden"
        )
        badge_text = box_canvas.create_text(
            int(10 * SCALE_FACTOR), int(8 * SCALE_FACTOR),
            text="LOG 0",
            font=("Helvetica", int(8 * SCALE_FACTOR), "bold"),
            fill="#ffd966",
            anchor="nw",
            state="hidden"
        )

        self.box_states.append({
            "previous_value": 0.0,
            "current_value": 0.0,
            "interpolating": False,
            "blink_state": False,
            "blinking_error": False,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None,
            "gas_type_text_id": gas_type_text_id,
            "full_scale": self.GAS_FULL_SCALE[gas_type_var.get()],
            "pwr_blink_state": False,
            "blink_thread": None,
            "stop_blinking": threading.Event(),
            "blink_lock": threading.Lock(),
            "alarm1_on": False,
            "alarm2_on": False,
            "last_logged_display": None,
            "last_logged_alarm1": None,
            "last_logged_alarm2": None,
            "last_logged_pwr": None,
            "segment_click_area": (seg_x1, seg_y1, seg_x2, seg_y2),
            "log_badge_bg": badge_bg,
            "log_badge_text": badge_text,
        })

        self.update_segment_display("    ", box_canvas, box_index=index)

        circle_items = []

        # AL1 lamp
        circle_items.append(
            box_canvas.create_oval(
                int(77 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
                int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                int(87 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
                int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
            )
        )
        box_canvas.create_text(
            int(140 * SCALE_FACTOR) - int(35 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="AL2",
            fill="#cccccc",
            anchor="e"
        )

        # AL2 lamp
        circle_items.append(
            box_canvas.create_oval(
                int(133 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
                int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                int(123 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
                int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
            )
        )
        box_canvas.create_text(
            int(95 * SCALE_FACTOR) - int(25 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="AL1",
            fill="#cccccc",
            anchor="e"
        )

        # PWR lamp
        circle_items.append(
            box_canvas.create_oval(
                int(30 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
                int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                int(40 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
                int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
            )
        )
        box_canvas.create_text(
            int(35 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="PWR",
            fill="#cccccc",
            anchor="center"
        )

        # FUT lamp
        circle_items.append(
            box_canvas.create_oval(
                int(171 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                int(181 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
            )
        )
        box_canvas.create_text(
            int(175 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            int(217 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="FUT",
            fill="#cccccc",
            anchor="n"
        )

        box_canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(270 * SCALE_FACTOR),
            text="GMS-1000",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )

        milliamp_var = StringVar(value="4-20 mA")
        milliamp_text_id = box_canvas.create_text(
            int(80 * SCALE_FACTOR), int(240 * SCALE_FACTOR),
            text=milliamp_var.get(),
            font=("Helvetica", int(10 * SCALE_FACTOR), "bold"),
            fill="#00ff00",
            anchor="center"
        )

        self.box_states[index]["milliamp_var"] = milliamp_var
        self.box_states[index]["milliamp_text_id"] = milliamp_text_id

        box_canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(295 * SCALE_FACTOR),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica", int(7 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )

        self.box_frames.append(box_frame)
        self.box_data.append((box_canvas, circle_items))

        self.update_log_badge(index)

    # -------------------------------------------------------------------------
    # 가스/램프/세그먼트
    # -------------------------------------------------------------------------
    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_data[box_index][0]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]["gas_type_text_id"], *position)
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

        self.maybe_log_event(
            box_index=box_index,
            raw_mA=self.box_states[box_index].get("current_value", 0.0),
            full_scale_value=0.0,
            gas_type=gas_type,
            alarm1=self.box_states[box_index].get("alarm1_on", False),
            alarm2=self.box_states[box_index].get("alarm2_on", False),
            pwr_on=self.box_states[box_index].get("current_value", 0.0) >= 1.5,
            event=f"GAS_TYPE_CHANGED:{gas_type}"
        )

    def update_circle_state(self, states, box_index=0):
        box_canvas, circle_items = self.box_data[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        if states[1]:
            states[0] = True

        for i, state in enumerate(states[:4]):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"analog_{box_index}")

        if states[1]:
            outline_color = outline_colors[1]
        elif states[0]:
            outline_color = outline_colors[0]
        elif states[3]:
            outline_color = outline_colors[3]
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

    def update_segment_display(self, value, box_canvas, blink=False, box_index=0):
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.box_states[box_index]["previous_segment_display"] = value
            self.schedule_segment_update(box_canvas, value, box_index, blink)

    def schedule_segment_update(self, box_canvas, value, box_index, blink):
        segment_queue = self.box_states[box_index].get("segment_queue", None)
        if not segment_queue:
            segment_queue = queue.Queue(maxsize=10)
            self.box_states[box_index]["segment_queue"] = segment_queue

        if not segment_queue.full():
            segment_queue.put((value, blink))

        if not self.box_states[box_index].get("segment_updating", False):
            self.box_states[box_index]["segment_updating"] = True
            self.parent.after(10, self.process_segment_queue, box_canvas, box_index)

    def process_segment_queue(self, box_canvas, box_index):
        segment_queue = self.box_states[box_index]["segment_queue"]
        if not segment_queue.empty():
            value, blink = segment_queue.get()
            self.perform_segment_update(box_canvas, value, blink, box_index)
            self.parent.after(10, self.process_segment_queue, box_canvas, box_index)
        else:
            self.box_states[box_index]["segment_updating"] = False

    def perform_segment_update(self, box_canvas, value, blink, box_index):
        value = value.strip()

        digits = []
        decimal_positions = [False, False, False, False]

        if '.' in value:
            dot_index = value.find('.')
            digits = list(value.replace('.', ''))
            digits = [' '] * (4 - len(digits)) + digits
            adjusted_dot_index = dot_index - (len(value) - 4)
            if 0 <= adjusted_dot_index < 4:
                decimal_positions[adjusted_dot_index] = True
        else:
            digits = list(value.rjust(4))

        leading_zero = True

        for index in range(4):
            if index >= len(digits):
                break

            digit = digits[index]

            if leading_zero and digit == '0' and index < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS.get(digit, SEGMENTS[' '])
                leading_zero = False

            if blink and self.box_states[box_index]["blink_state"]:
                segments = SEGMENTS[' ']

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_{chr(97 + j)}', fill=color)

            if decimal_positions[index]:
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_dot', fill='#fc0c0c')
            else:
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_dot', fill='#424242')

        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    # -------------------------------------------------------------------------
    # ADC 읽기
    # -------------------------------------------------------------------------
    async def read_adc_data(self):
        adc_addresses = [0x48, 0x49, 0x4B]
        adcs = []

        for addr in adc_addresses:
            try:
                adc = Adafruit_ADS1x15.ADS1115(address=addr)
                adc.read_adc(0, gain=GAIN)
                adcs.append(adc)
                print(f"ADC at address {hex(addr)} initialized successfully.")
            except Exception as e:
                print(f"ADC at address {hex(addr)} is not available: {e}")

        while True:
            tasks = []
            for adc_index, adc in enumerate(adcs):
                tasks.append(self.read_adc_values(adc, adc_index))
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)

    async def read_adc_values(self, adc, adc_index):
        try:
            values = []
            for channel in range(4):
                value = adc.read_adc(channel, gain=GAIN)
                voltage = value * 6.144 / 32767
                current = voltage / 250
                milliamp = current * 1000
                values.append(milliamp)

            for channel, milliamp in enumerate(values):
                box_index = adc_index * 4 + channel
                if box_index >= self.num_boxes:
                    continue

                if len(self.adc_values[box_index]) == 0:
                    filtered_value = milliamp
                else:
                    filtered_value = (0.7 * milliamp) + (0.3 * self.adc_values[box_index][-1])

                self.adc_values[box_index].append(filtered_value)

                print(f"Channel {box_index} Current: {filtered_value:.6f} mA")

                previous_value = self.box_states[box_index]["current_value"]
                current_value = filtered_value

                self.box_states[box_index]["previous_value"] = previous_value
                self.box_states[box_index]["current_value"] = current_value
                self.box_states[box_index]["interpolating"] = True

                self.adc_queue.put(box_index)

        except OSError as e:
            print(f"Error reading ADC data: {e}")
            base_box = adc_index * 4
            if 0 <= base_box < self.num_boxes:
                gas_type = self.gas_types.get(f"analog_box_{base_box}", StringVar(value="ORG")).get()
                self.maybe_log_event(
                    box_index=base_box,
                    raw_mA=0.0,
                    full_scale_value=0.0,
                    gas_type=gas_type,
                    alarm1=False,
                    alarm2=False,
                    pwr_on=False,
                    event=f"ADC_OSERROR:{e}"
                )

        except Exception as e:
            print(f"Unexpected error reading ADC data: {e}")
            base_box = adc_index * 4
            if 0 <= base_box < self.num_boxes:
                gas_type = self.gas_types.get(f"analog_box_{base_box}", StringVar(value="ORG")).get()
                self.maybe_log_event(
                    box_index=base_box,
                    raw_mA=0.0,
                    full_scale_value=0.0,
                    gas_type=gas_type,
                    alarm1=False,
                    alarm2=False,
                    pwr_on=False,
                    event=f"ADC_ERROR:{e}"
                )

    def start_adc_thread(self):
        adc_thread = threading.Thread(target=self.run_async_adc, daemon=True)
        adc_thread.start()

    def run_async_adc(self):
        asyncio.run(self.read_adc_data())

    # -------------------------------------------------------------------------
    # UI 업데이트
    # -------------------------------------------------------------------------
    def schedule_ui_update(self):
        self.parent.after(10, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        try:
            while not self.adc_queue.empty():
                box_index = self.adc_queue.get_nowait()
                gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
                gas_type = gas_type_var.get()
                full_scale = self.GAS_FULL_SCALE[gas_type]
                alarm_levels = self.ALARM_LEVELS[gas_type]

                self.start_interpolation(box_index, full_scale, alarm_levels)

        except Exception as e:
            print(f"Error updating UI from queue: {e}")

        self.schedule_ui_update()

    def start_interpolation(self, box_index, full_scale, alarm_levels):
        if self.box_states[box_index]["interpolating"]:
            prev_value = self.box_states[box_index]["previous_value"]
            curr_value = self.box_states[box_index]["current_value"]

            change = abs(curr_value - prev_value)
            SOME_THRESHOLD = 5.0

            if change > SOME_THRESHOLD:
                self.box_states[box_index]["interpolating"] = False
                self.update_display_immediately(box_index, curr_value, full_scale, alarm_levels)
            else:
                steps = 10
                interval = 10
                self.animate_step(box_index, 0, steps, prev_value, curr_value, full_scale, alarm_levels, interval)

    def animate_step(self, box_index, step, total_steps, prev_value, curr_value, full_scale, alarm_levels, interval):
        if step >= total_steps:
            self.box_states[box_index]["interpolating"] = False
            return

        interpolated_value = prev_value + (curr_value - prev_value) * (step / total_steps)
        formatted_value = ((interpolated_value - 4) / (20 - 4)) * full_scale
        formatted_value = max(0.0, min(formatted_value, full_scale))

        pwr_on = interpolated_value >= 1.5

        gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
        gas_type = gas_type_var.get()

        if gas_type == "HMDS":
            display_value = f"{formatted_value / 10:4.1f}"
        else:
            display_value = f"{int(formatted_value):>4}"

        self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
        self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

        self.update_circle_state(
            [
                self.box_states[box_index]["alarm1_on"],
                self.box_states[box_index]["alarm2_on"],
                pwr_on,
                False
            ],
            box_index=box_index
        )

        if pwr_on:
            self.update_segment_display(display_value, self.box_data[box_index][0], blink=False, box_index=box_index)
        else:
            self.update_segment_display("    ", self.box_data[box_index][0], blink=False, box_index=box_index)

        milliamp_text = f"{interpolated_value:.1f} mA" if pwr_on else "PWR OFF"
        milliamp_color = "#00ff00" if pwr_on else "#ff0000"
        self.box_states[box_index]["milliamp_var"].set(milliamp_text)

        box_canvas = self.box_data[box_index][0]
        box_canvas.itemconfig(
            self.box_states[box_index]["milliamp_text_id"],
            text=milliamp_text,
            fill=milliamp_color
        )

        # 마지막 단계에서만 기록
        if step == total_steps - 1:
            self.maybe_log_event(
                box_index=box_index,
                raw_mA=interpolated_value,
                full_scale_value=formatted_value,
                gas_type=gas_type,
                alarm1=self.box_states[box_index]["alarm1_on"],
                alarm2=self.box_states[box_index]["alarm2_on"],
                pwr_on=pwr_on
            )

        self.parent.after(
            interval,
            self.animate_step,
            box_index,
            step + 1,
            total_steps,
            prev_value,
            curr_value,
            full_scale,
            alarm_levels,
            interval
        )

    def update_display_immediately(self, box_index, current_value, full_scale, alarm_levels):
        formatted_value = ((current_value - 4) / (20 - 4)) * full_scale
        formatted_value = max(0.0, min(formatted_value, full_scale))

        pwr_on = current_value >= 1.5

        gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
        gas_type = gas_type_var.get()

        if gas_type == "HMDS":
            display_value = f"{formatted_value / 10:4.1f}"
        else:
            display_value = f"{int(formatted_value):>4}"

        self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
        self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

        self.update_circle_state(
            [
                self.box_states[box_index]["alarm1_on"],
                self.box_states[box_index]["alarm2_on"],
                pwr_on,
                False
            ],
            box_index=box_index
        )

        if pwr_on:
            self.update_segment_display(display_value, self.box_data[box_index][0], blink=False, box_index=box_index)
        else:
            self.update_segment_display("    ", self.box_data[box_index][0], blink=False, box_index=box_index)

        milliamp_text = f"{current_value:.1f} mA" if pwr_on else "PWR OFF"
        milliamp_color = "#00ff00" if pwr_on else "#ff0000"
        self.box_states[box_index]["milliamp_var"].set(milliamp_text)

        box_canvas = self.box_data[box_index][0]
        box_canvas.itemconfig(
            self.box_states[box_index]["milliamp_text_id"],
            text=milliamp_text,
            fill=milliamp_color
        )

        self.maybe_log_event(
            box_index=box_index,
            raw_mA=current_value,
            full_scale_value=formatted_value,
            gas_type=gas_type,
            alarm1=self.box_states[box_index]["alarm1_on"],
            alarm2=self.box_states[box_index]["alarm2_on"],
            pwr_on=pwr_on
        )

    # -------------------------------------------------------------------------
    # 경보 깜빡임
    # -------------------------------------------------------------------------
    def blink_alarm(self, box_index, is_second_alarm):
        def toggle_color():
            with self.box_states[box_index]["blink_lock"]:
                if is_second_alarm:
                    self.update_circle_state(
                        [True, self.box_states[box_index]["blink_state"], True, False],
                        box_index=box_index
                    )
                else:
                    self.update_circle_state(
                        [self.box_states[box_index]["blink_state"], False, True, False],
                        box_index=box_index
                    )

                self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

                if self.box_states[box_index]["current_value"] is not None:
                    self.update_segment_display(
                        str(self.box_states[box_index]["current_value"]),
                        self.box_data[box_index][0],
                        blink=False,
                        box_index=box_index
                    )

                if not self.box_states[box_index]["stop_blinking"].is_set():
                    if is_second_alarm:
                        self.parent.after(1000, toggle_color)
                    else:
                        self.parent.after(600, toggle_color)

        toggle_color()
