import os
import time
import threading
from collections import deque
from tkinter import Frame, Canvas, StringVar, Toplevel, Button
import Adafruit_ADS1x15
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplcursors
from common import SEGMENTS, create_segment_display
import queue
import asyncio

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
        self.alarm_callback = alarm_callback
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

        self.adc_values = [deque(maxlen=5) for _ in range(num_boxes)]  # 필터링을 위해 최근 5개의 값을 유지

        for i in range(num_boxes):
            self.create_analog_box(i)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        self.adc_queue = queue.Queue()
        self.start_adc_thread()
        self.schedule_ui_update()

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

        box_canvas = Canvas(box_frame, width=200, height=400, highlightthickness=4, highlightbackground="#000000", highlightcolor="#000000", bg='white')
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, 210, 250, fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, 250, 210, 410, fill='black', outline='grey', tags='border')

        gas_type_var = StringVar(value=self.gas_types.get(f"analog_box_{index}", "ORG"))
        gas_type_var.trace_add("write", lambda *args, var=gas_type_var, idx=index: self.update_full_scale(var, idx))
        self.gas_types[f"analog_box_{index}"] = gas_type_var.get()
        gas_type_text_id = box_canvas.create_text(*self.GAS_TYPE_POSITIONS[gas_type_var.get()], text=gas_type_var.get(), font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")
        self.box_states.append({
            "previous_value": 0,  # 마지막 실제 값을 저장
            "current_value": 0,  # 현재 보간된 값을 저장
            "interpolating": False,  # 현재 보간 중인지 여부
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
            "alarm2_on": False
        })

        create_segment_display(box_canvas)
        self.update_segment_display("    ", box_canvas, box_index=index)

        circle_items = []

        circle_items.append(box_canvas.create_oval(77, 200, 87, 190))
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(133, 200, 123, 190))
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(30, 200, 40, 190))
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(171, 200, 181, 190))
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        # GMS-1000 모델명
        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc", anchor="center")

        # 4~20mA 값 표시
        milliamp_var = StringVar(value="4-20 mA")
        milliamp_text_id = box_canvas.create_text(107, 330, text=milliamp_var.get(), font=("Helvetica", 14, "bold"), fill="#00ff00", anchor="center")
        self.box_states[index]["milliamp_var"] = milliamp_var
        self.box_states[index]["milliamp_text_id"] = milliamp_text_id

        # 사각형 LED 추가 (2개) - 중앙 기준으로 왼쪽과 오른쪽에 배치
        led1 = box_canvas.create_rectangle(0, 235, 105, 250, fill='#FF0000', outline='white')
        led2 = box_canvas.create_rectangle(103, 235, 205, 250, fill='#FF0000', outline='white')
        box_canvas.lift(led1)
        box_canvas.lift(led2)

        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc", anchor="center")

        self.box_frames.append((box_frame, box_canvas, circle_items, led1, led2, None))

        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=index: self.on_segment_click(i))

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_frames[box_index][1]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]["gas_type_text_id"], *position)
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

    def on_segment_click(self, box_index):
        threading.Thread(target=self.show_history_graph, args=(box_index,)).start()

    def update_circle_state(self, states, box_index=0):
        _, box_canvas, circle_items, led1, led2, _ = self.box_frames[box_index]
        
        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        if states[1]:  # AL2가 활성화된 경우
            states[0] = True  # AL1은 항상 켜져 있어야 함

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active)

        if states[1]:
            outline_color = outline_colors[1]  # AL2의 색상
        elif states[0]:
            outline_color = outline_colors[0]  # AL1의 색상
        elif states[3]:
            outline_color = outline_colors[3]  # FUT의 색상
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

        # 사각형 LED 상태 업데이트
        box_canvas.itemconfig(led1, fill='red' if states[0] else 'black')
        box_canvas.itemconfig(led2, fill='red' if states[1] else 'black')

    def update_segment_display(self, value, box_canvas, blink=False, box_index=0):
        value = value.zfill(4)
        leading_zero = True
        blink_state = self.box_states[box_index]["blink_state"]
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.record_history(box_index, value)
            self.box_states[box_index]["previous_segment_display"] = value

        for i, digit in enumerate(value):
            if leading_zero and digit == '0' and i < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS[digit]
                leading_zero = False

            if blink and blink_state:
                segments = SEGMENTS[' ']

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{i}_{chr(97 + j)}', fill=color)

        self.box_states[box_index]["blink_state"] = not blink_state

    def record_history(self, box_index, value):
        if value.strip():
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            log_line = f"{timestamp},{value}\n"
            log_file_index = self.get_log_file_index(box_index)
            log_file = os.path.join(self.history_dir, f"box_{box_index}_{log_file_index}.log")

            threading.Thread(target=self.async_write_log, args=(log_file, log_line)).start()

    def async_write_log(self, log_file, log_line):
        with open(log_file, 'a') as file:
            file.write(log_line)

    def get_log_file_index(self, box_index):
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
        log_entries = []
        log_file = os.path.join(self.history_dir, f"box_{box_index}_{file_index}.log")
        if os.path.exists(log_file):
            with open(log_file, 'r') as file:
                lines = file.readlines()
                for line in lines:
                    timestamp, value = line.strip().split(',')
                    log_entries.append((timestamp, value))
        return log_entries

    def show_history_graph(self, box_index):
        with self.history_lock:
            if self.history_window and self.history_window.winfo_exists():
                self.history_window.destroy()

            self.history_window = Toplevel(self.root)
            self.history_window.title(f"History - Box {box_index}")
            self.history_window.geometry("1200x800")
            self.history_window.attributes("-topmost", True)

            self.current_file_index = self.get_log_file_index(box_index) - 1
            self.update_history_graph(box_index, self.current_file_index)

    def update_history_graph(self, box_index, file_index):
        log_entries = self.load_log_files(box_index, file_index)
        times, values = zip(*log_entries) if log_entries else ([], [])

        figure = plt.Figure(figsize=(12, 8), dpi=100)
        ax = figure.add_subplot(111)

        ax.plot(times, values, marker='o')
        ax.set_title(f'History - Box {box_index} (File {file_index + 1})')
        ax.set_xlabel('Time')
        ax.set_ylabel('Value')
        figure.autofmt_xdate()

        if hasattr(self, 'canvas'):
            self.canvas.get_tk_widget().destroy()

        self.canvas = FigureCanvasTkAgg(figure, master=self.history_window)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

        if not hasattr(self, 'nav_frame'):
            self.nav_frame = Frame(self.history_window)
            self.nav_frame.pack(side="bottom")

            self.prev_button = Button(self.nav_frame, text="<", command=lambda: self.navigate_logs(box_index, -1))
            self.prev_button.pack(side="left")

            self.next_button = Button(self.nav_frame, text=">", command=lambda: self.navigate_logs(box_index, 1))
            self.next_button.pack(side="right")

        mplcursors.cursor(ax)

    def navigate_logs(self, box_index, direction):
        self.current_file_index += direction
        if (self.current_file_index) < 0:
            self.current_file_index = 0
        elif self.current_file_index >= self.get_log_file_index(box_index):
            self.current_file_index = self.get_log_file_index(box_index) - 1

        self.update_history_graph(box_index, self.current_file_index)

    async def read_adc_data(self):
        adc_addresses = [0x48, 0x4A, 0x4B]
        adcs = [Adafruit_ADS1x15.ADS1115(address=addr) for addr in adc_addresses]
        while True:
            tasks = []
            for adc_index, adc in enumerate(adcs):
                task = self.read_adc_values(adc, adc_index)
                tasks.append(task)
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)  # 샘플링 속도: 100ms 간격으로 데이터 수집

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

                self.adc_values[box_index].append(milliamp)

                # 필터링을 통해 급격한 변화를 완화
                filtered_value = sum(self.adc_values[box_index]) / len(self.adc_values[box_index])

                if len(self.adc_values[box_index]) == 5:  # 필터링을 위한 최소 값이 모였을 때
                    previous_value = self.box_states[box_index]["current_value"]
                    current_value = filtered_value

                    self.box_states[box_index]["previous_value"] = previous_value
                    self.box_states[box_index]["current_value"] = current_value
                    self.box_states[box_index]["interpolating"] = True

                    self.adc_queue.put(box_index)
        except OSError as e:
            print(f"Error reading ADC data: {e}")
        except Exception as e:
            print(f"Unexpected error reading ADC data: {e}")

    def start_adc_thread(self):
        loop = asyncio.get_event_loop()
        adc_thread = threading.Thread(target=loop.run_until_complete, args=(self.read_adc_data(),))
        adc_thread.daemon = True
        adc_thread.start()

    def schedule_ui_update(self):
        self.root.after(10, self.update_ui_from_queue)  # 10ms 간격으로 UI 업데이트 예약

    def update_ui_from_queue(self):
        try:
            while not self.adc_queue.empty():
                box_index = self.adc_queue.get_nowait()
                gas_type = self.gas_types.get(f"analog_box_{box_index}", "ORG")
                full_scale = self.GAS_FULL_SCALE[gas_type]
                alarm_levels = self.ALARM_LEVELS[gas_type]

                # 애니메이션 보간
                def interpolate_values():
                    if self.box_states[box_index]["interpolating"]:
                        prev_value = self.box_states[box_index]["previous_value"]
                        curr_value = self.box_states[box_index]["current_value"]

                        # 3단계로 나누어 1ms 간격으로 보간
                        for i in range(1, 4):
                            interpolated_value = prev_value + (curr_value - prev_value) * (i / 3.0)
                            formatted_value = int((interpolated_value - 4) / (20 - 4) * full_scale)
                            formatted_value = max(0, min(formatted_value, full_scale))

                            pwr_on = interpolated_value >= 1.5

                            self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
                            self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

                            self.update_circle_state([self.box_states[box_index]["alarm1_on"], self.box_states[box_index]["alarm2_on"], pwr_on, False], box_index=box_index)

                            # 세그먼트 디스플레이에 값을 반영
                            if pwr_on:
                                self.update_segment_display(str(int(formatted_value)).zfill(4), self.box_frames[box_index][1], blink=False, box_index=box_index)
                            else:
                                self.update_segment_display("    ", self.box_frames[box_index][1], blink=False, box_index=box_index)

                            # 4~20mA 값 업데이트
                            milliamp_text = f"{interpolated_value:.1f} mA" if pwr_on else "PWR OFF"
                            self.box_states[box_index]["milliamp_var"].set(milliamp_text)
                            box_canvas = self.box_frames[box_index][1]
                            box_canvas.itemconfig(self.box_states[box_index]["milliamp_text_id"], text=milliamp_text)

                            self.root.update_idletasks()
                            time.sleep(0.001)  # 1ms 간격으로 업데이트

                        self.box_states[box_index]["interpolating"] = False

                interpolate_values()

        except Exception as e:
            print(f"Error updating UI from queue: {e}")

        self.schedule_ui_update()  # 다음 업데이트 예약

    def blink_alarm(self, box_index, is_second_alarm):
        def toggle_color():
            with self.box_states[box_index]["blink_lock"]:
                if is_second_alarm:
                    # 2차 알람 조건
                    self.update_circle_state([True, self.box_states[box_index]["blink_state"], True, False], box_index=box_index)
                else:
                    # 1차 알람 조건
                    self.update_circle_state([self.box_states[box_index]["blink_state"], False, True, False], box_index=box_index)

                self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

                # 세그먼트 디스플레이를 깜빡이지 않고 그대로 유지
                if self.box_states[box_index]["current_value"] is not None:
                    self.update_segment_display(str(self.box_states[box_index]["current_value"]).zfill(4), self.box_frames[box_index][1], blink=False, box_index=box_index)

                if not self.box_states[box_index]["stop_blinking"].is_set():
                    self.root.after(1000, toggle_color) if is_second_alarm else self.root.after(600, toggle_color)

        toggle_color()

if __name__ == "__main__":
    from tkinter import Tk
    import json

    with open('settings.json') as f:
        settings = json.load(f)

    root = Tk()
    main_frame = Frame(root)
    main_frame.pack()

    analog_boxes = settings["analog_boxes"]
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["analog_gas_types"], alarm_callback=lambda active: print("Alarm Active" if active else "Alarm Inactive"))

    root.mainloop()
