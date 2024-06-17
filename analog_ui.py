import os
import time
import threading
from tkinter import Frame, Canvas, StringVar, Toplevel, Button
import Adafruit_ADS1x15
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplcursors
from common import SEGMENTS, create_segment_display
import queue

class AnalogUI:
    LOGS_PER_FILE = 10  # 로그 파일당 저장할 로그 개수

    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }

    ALARM_LEVELS = {
        "ORG": {"AL1": 9500, "AL2": 9999},
        "ARF-T": {"AL1": 2000, "AL2": 4000},
        "HMDS": {"AL1": 2640, "AL2": 3000},
        "HC-100": {"AL1": 1500, "AL2": 3000}
    }

    def __init__(self, root, num_boxes, gas_types):
        self.root = root
        self.gas_types = gas_types
        self.num_boxes = num_boxes
        self.box_states = []
        self.histories = [[] for _ in range(num_boxes)]
        self.graph_windows = [None for _ in range(num_boxes)]
        self.history_window = None  # 히스토리 창을 저장할 변수
        self.history_lock = threading.Lock()  # 히스토리 창 중복 방지를 위한 락

        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=40, pady=40)

        self.row_frames = []
        self.box_frames = []
        self.history_dir = "analog_history_logs"

        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        self.adc_values = [[] for _ in range(num_boxes)]  # 각 박스에 대한 최근 ADC 값을 저장할 리스트

        for i in range(num_boxes):
            self.create_analog_box(i)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        self.adc_queue = queue.Queue()
        self.start_adc_thread()
        self.start_ui_update_thread()

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
        gas_type_text_id = box_canvas.create_text(129, 105, text=gas_type_var.get(), font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None,
            "gas_type_text_id": gas_type_text_id,
            "full_scale": self.GAS_FULL_SCALE[gas_type_var.get()],
            "pwr_blink_state": False,  # PWR 깜빡임 상태 초기화
            "last_value": None,  # 마지막 값을 저장하는 상태 추가
            "blink_thread": None,  # 깜빡임을 처리하는 스레드 추가
            "stop_blinking": threading.Event(),  # 깜빡임을 중지하는 이벤트 추가
            "blink_lock": threading.Lock(),  # 깜빡임 상태를 보호하는 락 추가
            "alarm1_on": False,  # 알람1 상태
            "alarm2_on": False  # 알람2 상태
        })

        create_segment_display(box_canvas)
        self.update_segment_display("    ", box_canvas, box_index=index)

        circle_items = []

        circle_items.append(box_canvas.create_oval(133, 200, 123, 190))
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(77, 200, 87, 190))
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(30, 200, 40, 190))
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(171, 200, 181, 190))
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc", anchor="center")

        self.box_frames.append((box_frame, box_canvas, circle_items, None, None, None))

        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=index: self.on_segment_click(i))

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_frames[box_index][1]
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

    def on_segment_click(self, box_index):
        threading.Thread(target=self.show_history_graph, args=(box_index,)).start()

    def update_circle_state(self, states, box_index=0):
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        if states[0]:
            outline_color = outline_colors[0]
        elif states[1]:
            outline_color = outline_colors[1]
        elif states[3]:
            outline_color = outline_colors[3]
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

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
        if self.current_file_index < 0:
            self.current_file_index = 0
        elif self.current_file_index >= self.get_log_file_index(box_index):
            self.current_file_index = self.get_log_file_index(box_index) - 1

        self.update_history_graph(box_index, self.current_file_index)

    def read_adc_data(self):
    adc_addresses = [0x48, 0x49, 0x4A, 0x4B]
    adcs = [Adafruit_ADS1x15.ADS1115(address=addr) for addr in adc_addresses]
    GAIN = 2 / 3
    while True:
        for adc_index, adc in enumerate(adcs):
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

                    if len(self.adc_values[box_index]) >= 10:
                        self.adc_values[box_index].pop(0)
                    self.adc_values[box_index].append(milliamp)

                    avg_milliamp = sum(self.adc_values[box_index]) / len(self.adc_values[box_index])
                    print(f"Box {box_index}: {avg_milliamp} mA")
                    self.adc_queue.put((box_index, avg_milliamp))
            except Exception as e:
                print(f"Error reading ADC data: {e}")

        time.sleep(0.1)



                    # 최근 10개의 값을 저장
                    if len(self.adc_values[box_index]) >= 10:
                        self.adc_values[box_index].pop(0)
                    self.adc_values[box_index].append(milliamp)

                    # 최근 값의 평균을 사용
                    avg_milliamp = sum(self.adc_values[box_index]) / len(self.adc_values[box_index])
                    self.adc_queue.put((box_index, avg_milliamp))

            time.sleep(1)

    def start_adc_thread(self):
        adc_thread = threading.Thread(target=self.read_adc_data)
        adc_thread.daemon = True
        adc_thread.start()

    def start_ui_update_thread(self):
        ui_update_thread = threading.Thread(target=self.update_ui_from_queue)
        ui_update_thread.daemon = True
        ui_update_thread.start()

    def update_ui_from_queue(self):
    while True:
        try:
            box_index, avg_milliamp = self.adc_queue.get(timeout=1)
            gas_type = self.gas_types.get(f"analog_box_{box_index}", "ORG")
            full_scale = self.GAS_FULL_SCALE[gas_type]
            alarm_levels = self.ALARM_LEVELS[gas_type]
            formatted_value = int((avg_milliamp - 4) / (20 - 4) * full_scale)
            formatted_value = max(0, min(formatted_value, full_scale))

            pwr_on = avg_milliamp >= 1.5

            self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
            self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

            self.update_circle_state([self.box_states[box_index]["alarm1_on"], self.box_states[box_index]["alarm2_on"], pwr_on, False], box_index=box_index)
            self.box_states[box_index]["last_value"] = formatted_value

            if pwr_on:
                if self.box_states[box_index]["alarm2_on"] or self.box_states[box_index]["alarm1_on"]:
                    if not self.box_states[box_index]["blinking_error"]:
                        self.box_states[box_index]["blinking_error"] = True
                        self.box_states[box_index]["stop_blinking"].clear()
                        if self.box_states[box_index]["blink_thread"] is None or not self.box_states[box_index]["blink_thread"].is_alive():
                            self.box_states[box_index]["blink_thread"] = threading.Thread(target=self.blink_alarm, args=(box_index,))
                            self.box_states[box_index]["blink_thread"].start()
                else:
                    with self.box_states[box_index]["blink_lock"]:
                        self.update_segment_display(str(formatted_value).zfill(4), self.box_frames[box_index][1], box_index=box_index)
                        self.box_states[box_index]["blinking_error"] = False
                        self.box_states[box_index]["stop_blinking"].set()
            else:
                with self.box_states[box_index]["blink_lock"]:
                    self.update_segment_display("    ", self.box_frames[box_index][1], box_index=box_index)
                    self.box_states[box_index]["blinking_error"] = False
                    self.box_states[box_index]["stop_blinking"].set()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"Error updating UI from queue: {e}")


    def blink_alarm(self, box_index):
        def toggle_color():
            with self.box_states[box_index]["blink_lock"]:
                if self.box_states[box_index]["alarm2_on"]:
                    self.update_circle_state([False, self.box_states[box_index]["blink_state"], True, False], box_index=box_index)
                elif self.box_states[box_index]["alarm1_on"]:
                    self.update_circle_state([self.box_states[box_index]["blink_state"], False, True, False], box_index=box_index)
                self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]
                if self.box_states[box_index]["last_value"] is not None:
                    self.update_segment_display(str(self.box_states[box_index]["last_value"]).zfill(4), self.box_frames[box_index][1], blink=self.box_states[box_index]["blink_state"], box_index=box_index)
                if not self.box_states[box_index]["stop_blinking"].is_set():
                    self.root.after(600, toggle_color)

        toggle_color()

# main.py에서 AnalogUI 클래스의 인스턴스를 생성하는 코드
if __name__ == "__main__":
    from tkinter import Tk
    import json

    with open('settings.json') as f:
        settings = json.load(f)

    root = Tk()
    main_frame = Frame(root)
    main_frame.pack()

    analog_boxes = settings["analog_boxes"]
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["analog_gas_types"])

    root.mainloop()
