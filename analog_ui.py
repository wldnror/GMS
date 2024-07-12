import os
import time
import threading
from collections import deque
from tkinter import Frame, Canvas, StringVar, Tk
import Adafruit_ADS1x15
from common import SEGMENTS, create_segment_display, update_full_scale, on_segment_click, update_segment_display as common_update_segment_display, load_log_files, show_history_graph, update_history_graph
import queue
import json
from concurrent.futures import ThreadPoolExecutor

GAIN = 2 / 3  # 전역 변수로 설정

class AnalogUI:
    LOGS_PER_FILE = 10

    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG": (149, 117),
        "ARF-T": (140, 117),
        "HMDS": (143, 117),
        "HC-100": (137, 117)
    }

    ALARM_LEVELS = {
        "ORG": {"AL1": 9500, "AL2": 9999},
        "ARF-T": {"AL1": 2000, "AL2": 4000},
        "HMDS": {"AL1": 2640, "AL2": 3000},
        "HC-100": {"AL1": 1500, "AL2": 3000}
    }

    def __init__(self, root, num_boxes, gas_types, alarm_callback):
        self.root = root
        self.alarm_callback = alarm_callback  # 알람 콜백 추가
        self.gas_types = gas_types
        self.num_boxes = num_boxes
        self.box_states = []
        self.histories = [[] for _ in range(num_boxes)]
        self.graph_windows = [None for _ in range(num_boxes)]
        self.history_window = None
        self.history_lock = threading.Lock()

        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=40, pady=40)

        self.row_frames = []
        self.box_frames = []
        self.history_dir = "analog_history_logs"

        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        self.adc_values = [deque(maxlen=30) for _ in range(num_boxes)]  # deque with maxlen of 30

        for i in range(num_boxes):
            self.create_analog_box(i)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        self.adc_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.start_adc_thread()
        self.schedule_alarm_update()

    def create_analog_box(self, index):
        row = index // 7
        col = index % 7

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0)
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame)
        box_frame.grid(row=0, column=col, padx=20, pady=20)

        box_canvas = Canvas(box_frame, width=200, height=400, highlightthickness=4, highlightbackground="#000000", highlightcolor="#000000")
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, 210, 250, fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, 250, 210, 410, fill='black', outline='grey', tags='border')

        gas_type_var = StringVar(value=self.gas_types.get(f"analog_box_{index}", "ORG"))
        gas_type_var.trace_add("write", lambda *args, var=gas_type_var, idx=index: self.update_full_scale(var, idx))
        self.gas_types[f"analog_box_{index}"] = gas_type_var.get()
        gas_type_text_id = box_canvas.create_text(*self.GAS_TYPE_POSITIONS[gas_type_var.get()], text=gas_type_var.get(), font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None,
            "gas_type_text_id": gas_type_text_id,
            "full_scale": self.GAS_FULL_SCALE[gas_type_var.get()],
            "pwr_blink_state": False,
            "last_value": None,
            "blink_thread": None,
            "stop_blinking": threading.Event(),
            "blink_lock": threading.Lock(),
            "alarm1_on": False,
            "alarm2_on": False,
            "last_alarm1_state": False,  # 마지막 AL1 상태 추가
            "last_alarm2_state": False  # 마지막 AL2 상태 추가
        })

        create_segment_display(box_canvas)
        common_update_segment_display(self, "    ", box_canvas, box_index=index)

        circle_items = []

        circle_items.append(box_canvas.create_oval(77, 200, 87, 190))
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(133, 200, 123, 190))
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(30, 200, 40, 190))
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(171, 200, 181, 190))
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc", anchor="center")

        self.box_frames.append((box_frame, box_canvas, circle_items, None, None, None))

        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=index: self.on_segment_click(i))

    def record_history(self, box_index, value):
        if value.strip():
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            log_line = f"{timestamp},{value}\n"
            log_file_index = self.get_log_file_index(box_index)
            log_file = os.path.join(self.history_dir, f"box_{box_index}_{log_file_index}.log")

            # 비동기적으로 로그 파일에 기록
            threading.Thread(target=self.async_write_log, args=(log_file, log_line)).start()

    def async_write_log(self, log_file, log_line):
        with open(log_file, 'a') as file:
            file.write(log_line)

    def get_log_file_index(self, box_index):
        """현재 로그 파일 인덱스를 반환하고, 로그 파일이 가득 차면 새로운 인덱스를 반환"""
        index = 0
        while True:
            log_file = os.path.join(self.history_dir, f"box_{box_index}_{index}.log")
            if not os.path.exists(log_file):
                return index
            with open(log_file, 'r') as file:
                lines = file.readlines()
                if len(lines) < self.LOGS_PER_FILE:
                    return index
            index += 1

    def load_log_files(self, box_index, file_index):
        """특정 로그 파일을 로드하여 로그 목록을 반환"""
        log_entries = []
        log_file = os.path.join(self.history_dir, f"box_{box_index}_{file_index}.log")
        if os.path.exists(log_file):
            with open(log_file, 'r') as file:
                lines = file.readlines()
                for line in lines:
                    timestamp, value = line.strip().split(',')
                    log_entries.append((timestamp, value))
        return log_entries

    def navigate_logs(self, box_index, direction):
        self.current_file_index += direction
        if self.current_file_index < 0:
            self.current_file_index = 0
        elif self.current_file_index >= self.get_log_file_index(box_index):
            self.current_file_index = self.get_log_file_index(box_index) - 1

        self.update_history_graph(box_index, self.current_file_index)

    def read_adc_data(self):
        adc_addresses = [0x48, 0x49, 0x4A, 0x4B]
        adcs = [Adafruit_ADS1x15.ADS1115(address=addr) for addr in adc_addresses]
        while True:
            try:
                for adc_index, adc in enumerate(adcs):
                    self.read_adc_values(adc, adc_index)
                time.sleep(0.1)  # 샘플링 속도 증가
            except Exception as e:
                print(f"Error reading ADC data: {e}")

    def read_adc_values(self, adc, adc_index):
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

                self.adc_values[box_index].append(milliamp)

                avg_milliamp = sum(self.adc_values[box_index]) / len(self.adc_values[box_index])
                print(f"Box {box_index}: {avg_milliamp} mA")
                self.adc_queue.put((box_index, avg_milliamp))
        except Exception as e:
            print(f"Error reading ADC values: {e}")

    def update_circle_state(self, states, box_index=0):
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        for i, state in enumerate(states):
            if i == 1 and states[0]:  # AL1이 켜진 상태에서 AL2가 깜빡이면
                color = 'red' if state else colors_off[i]
            else:
                color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active)

        if states[0]:
            outline_color = outline_colors[0]
        elif states[1]:
            outline_color = outline_colors[1]
        elif states[3]:
            outline_color = outline_colors[3]
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

    def start_adc_thread(self):
        adc_thread = threading.Thread(target=self.read_adc_data)
        adc_thread.daemon = True
        adc_thread.start()

    def schedule_alarm_update(self):
        self.root.after(500, self.update_alarm_from_queue)  # 500ms 간격으로 알람 업데이트 예약

    def update_alarm_from_queue(self):
        try:
            while not self.adc_queue.empty():
                box_index, avg_milliamp = self.adc_queue.get_nowait()
                self.executor.submit(self.update_alarm_state, box_index, avg_milliamp)
        except Exception as e:
            print(f"Error updating alarm from queue: {e}")

        self.schedule_alarm_update()  # 다음 업데이트 예약

    def update_alarm_state(self, box_index, avg_milliamp):
        print(f"Box {box_index} average milliamp: {avg_milliamp}")  # Debugging line
        gas_type = self.gas_types.get(f"analog_box_{box_index}", "ORG")
        full_scale = self.GAS_FULL_SCALE[gas_type]
        alarm_levels = self.ALARM_LEVELS[gas_type]

        if avg_milliamp < 1.8:
            formatted_value = "    "  # 1.8mA 이하의 값에 대해 모든 세그먼트 꺼짐
        # 1.8~2.5mA 사이의 값은 "REST"로 표시
        elif 1.8 <= avg_milliamp <= 3.5:
            formatted_value = "REST"
        # 4mA 미만의 값은 0으로 표시
        else:
            formatted_value = int((avg_milliamp - 4) / (20 - 4) * full_scale)
            formatted_value = max(0, min(formatted_value, full_scale))

        print(f"Formatted value: {formatted_value}")  # Debugging line
        pwr_on = avg_milliamp >= 1.5

        self.box_states[box_index]["last_value"] = formatted_value

        alarm1_on = formatted_value != "    " and formatted_value != "REST" and formatted_value >= alarm_levels["AL1"]
        alarm2_on = formatted_value != "    " and formatted_value != "REST" and formatted_value >= alarm_levels["AL2"] if pwr_on else False

        self.root.after(0, common_update_segment_display, self, str(formatted_value).zfill(4) if isinstance(formatted_value, int) else formatted_value, self.box_frames[box_index][1], False, box_index)

        # AL2 상태 변화 체크
        if alarm2_on != self.box_states[box_index]["last_alarm2_state"]:
            self.box_states[box_index]["alarm2_on"] = alarm2_on
            self.box_states[box_index]["stop_blinking"].clear() if alarm2_on else self.box_states[box_index]["stop_blinking"].set()
            self.start_blinking(box_index, True) if alarm2_on else self.root.after(0, self.update_circle_state, [alarm1_on, False, pwr_on, False], box_index)
            self.box_states[box_index]["last_alarm2_state"] = alarm2_on

        # AL1 상태 변화 체크
        if alarm1_on != self.box_states[box_index]["last_alarm1_state"]:
            self.box_states[box_index]["alarm1_on"] = alarm1_on
            self.box_states[box_index]["stop_blinking"].clear() if alarm1_on else self.box_states[box_index]["stop_blinking"].set()
            self.start_blinking(box_index, False) if alarm1_on else self.root.after(0, self.update_circle_state, [False, alarm2_on, pwr_on, False], box_index)
            self.box_states[box_index]["last_alarm1_state"] = alarm1_on

        # 알람 상태가 모두 꺼진 경우
        if not alarm1_on and not alarm2_on:
            self.root.after(0, self.update_circle_state, [False, False, pwr_on, False], box_index)

    def start_blinking(self, box_index, is_second_alarm):
        def toggle_color():
            with self.box_states[box_index]["blink_lock"]:
                self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]
                if is_second_alarm:
                    # AL2 깜빡임
                    self.root.after(0, self.update_circle_state, [True, self.box_states[box_index]["blink_state"], True, False], box_index)
                else:
                    # AL1 깜빡임
                    self.root.after(0, self.update_circle_state, [self.box_states[box_index]["blink_state"], False, True, False], box_index)

                # 정해진 간격으로 깜빡임을 유지
                if not self.box_states[box_index]["stop_blinking"].is_set():
                    self.root.after(1000, toggle_color)

        if not self.box_states[box_index]["blink_thread"] or not self.box_states[box_index]["blink_thread"].is_alive():
            self.box_states[box_index]["blink_thread"] = threading.Thread(target=toggle_color)
            self.box_states[box_index]["blink_thread"].start()

if __name__ == "__main__":
    def set_alarm_status(active):
        if active:
            print("Alarm is active!")
        else:
            print("Alarm is inactive!")

    with open('settings.json') as f:
        settings = json.load(f)

    root = Tk()
    main_frame = Frame(root)
    main_frame.pack()

    analog_boxes = settings["analog_boxes"]
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["analog_gas_types"], set_alarm_status)

    root.mainloop()
