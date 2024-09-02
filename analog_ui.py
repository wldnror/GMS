import os
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

# 전역 변수로 설정
GAIN = 2 / 3  
SCALE_FACTOR = 1.65  # 20% 키우기

class AnalogUI:
    LOGS_PER_FILE = 10

    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG": (115, 95),
        "ARF-T": (107, 95),
        "HMDS": (110, 95),
        "HC-100": (104, 95)
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
        self.box_frame.grid(row=0, column=0)

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
        # 한 행에 몇 개의 박스를 배치할지 결정하는 변수
        max_boxes_per_row = 6  # 여기를 6으로 설정

        # 행과 열을 계산하는 부분
        row = index // max_boxes_per_row
        col = index % max_boxes_per_row

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0)  # 간격 제거
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame, highlightthickness=int(2.5 * SCALE_FACTOR))  # 투명 테두리 부분 추가
        box_frame.grid(row=0, column=col)  # 간격 제거

        inner_frame = Frame(box_frame)  # 실제 내부 Frame
        inner_frame.pack(padx=int(2.5 * SCALE_FACTOR), pady=int(2.5 * SCALE_FACTOR))  # 투명 테두리만큼 패딩 추가

        box_canvas = Canvas(inner_frame, width=int(150 * SCALE_FACTOR), height=int(300 * SCALE_FACTOR), 
                            highlightthickness=int(3 * SCALE_FACTOR),
                            highlightbackground="#000000", highlightcolor="#000000", bg='white')
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR), fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, int(200 * SCALE_FACTOR), int(160 * SCALE_FACTOR), int(310 * SCALE_FACTOR), fill='black', outline='grey', tags='border')

        gas_type_var = StringVar(value=self.gas_types.get(f"analog_box_{index}", "ORG"))
        gas_type_var.trace_add("write", lambda *args, var=gas_type_var, idx=index: self.update_full_scale(var, idx))
        self.gas_types[f"analog_box_{index}"] = gas_type_var.get()
        gas_type_text_id = box_canvas.create_text(*[int(coord * SCALE_FACTOR) for coord in self.GAS_TYPE_POSITIONS[gas_type_var.get()]],
                                                  text=gas_type_var.get(), font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")
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

        # 수정된 코드: AL1과 AL2의 텍스트와 원 위치를 교체

        circle_items.append(box_canvas.create_oval(int(77 * SCALE_FACTOR) - int(20 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(87 * SCALE_FACTOR) - int(20 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(140 * SCALE_FACTOR) - int(35 * SCALE_FACTOR), int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="AL2", fill="#cccccc", anchor="e")
        
        circle_items.append(box_canvas.create_oval(int(133 * SCALE_FACTOR) - int(30 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(123 * SCALE_FACTOR) - int(30 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(95 * SCALE_FACTOR) - int(25 * SCALE_FACTOR), int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(int(30 * SCALE_FACTOR) - int(10 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(40 * SCALE_FACTOR) - int(10 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(35 * SCALE_FACTOR) - int(10 * SCALE_FACTOR), int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(int(171 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(181 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(175 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), int(217 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="FUT", fill="#cccccc", anchor="n")

        # GMS-1000 모델명
        box_canvas.create_text(int(80 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="GMS-1000", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")

        # 4~20mA 값 표시
        milliamp_var = StringVar(value="4-20 mA")
        milliamp_text_id = box_canvas.create_text(int(80 * SCALE_FACTOR), int(240 * SCALE_FACTOR), text=milliamp_var.get(), font=("Helvetica", int(10 * SCALE_FACTOR), "bold"), fill="#00ff00", anchor="center")
        self.box_states[index]["milliamp_var"] = milliamp_var
        self.box_states[index]["milliamp_text_id"] = milliamp_text_id

        # 사각형 LED 추가 (2개) - 중앙 기준으로 왼쪽과 오른쪽에 배치
        led1 = box_canvas.create_rectangle(0, int(200 * SCALE_FACTOR), int(78 * SCALE_FACTOR), int(215 * SCALE_FACTOR), fill='#FF0000', outline='white')
        led2 = box_canvas.create_rectangle(int(78 * SCALE_FACTOR), int(200 * SCALE_FACTOR), int(155 * SCALE_FACTOR), int(215 * SCALE_FACTOR), fill='#FF0000', outline='white')
        box_canvas.lift(led1)
        box_canvas.lift(led2)

        box_canvas.create_text(int(80 * SCALE_FACTOR), int(295 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD", font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")

        self.box_frames.append((box_frame, box_canvas, circle_items, led1, led2, None))

        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=index: self.on_segment_click(i))

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_frames[box_index][1]
        position = [int(coord * SCALE_FACTOR) for coord in self.GAS_TYPE_POSITIONS[gas_type]]
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
        self.alarm_callback(alarm_active, box_index)  # box_index를 추가로 전달

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
        value = value.zfill(4)  # 네 자리로 맞추기
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        # 큐를 사용하여 세그먼트 업데이트를 관리하여 UI 멈춤 방지
        if value != previous_segment_display:
            self.box_states[box_index]["previous_segment_display"] = value
            self.schedule_segment_update(box_canvas, value, box_index, blink)

    def schedule_segment_update(self, box_canvas, value, box_index, blink):
        segment_queue = self.box_states[box_index].get("segment_queue", None)
        if not segment_queue:
            segment_queue = queue.Queue(maxsize=10)
            self.box_states[box_index]["segment_queue"] = segment_queue
        
        # 세그먼트 업데이트 요청을 큐에 넣기
        if not segment_queue.full():
            segment_queue.put((value, blink))

        # 큐에서 업데이트 실행
        if not self.box_states[box_index].get("segment_updating", False):
            self.box_states[box_index]["segment_updating"] = True
            self.root.after(10, self.process_segment_queue, box_canvas, box_index)

    def process_segment_queue(self, box_canvas, box_index):
        segment_queue = self.box_states[box_index]["segment_queue"]
        if not segment_queue.empty():
            value, blink = segment_queue.get()
            self.perform_segment_update(box_canvas, value, blink, box_index)
            self.root.after(10, self.process_segment_queue, box_canvas, box_index)
        else:
            self.box_states[box_index]["segment_updating"] = False

    def perform_segment_update(self, box_canvas, value, blink, box_index):
        # 각 자리의 숫자를 순차적으로 업데이트하는 애니메이션
        def update_digit(index, leading_zero=True):
            if index >= len(value):
                return  # 모든 자릿수 업데이트가 완료된 경우

            digit = value[index]

            if leading_zero and digit == '0' and index < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS[digit]
                leading_zero = False

            if blink and self.box_states[box_index]["blink_state"]:
                segments = SEGMENTS[' ']

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_{chr(97 + j)}', fill=color)

            # 다음 자릿수를 일정 시간 후에 업데이트
            self.root.after(10, lambda: update_digit(index + 1, leading_zero))

        # 애니메이션 시작: 일의 자리부터 업데이트
        update_digit(0)

        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

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
            self.history_window.geometry(f"{int(1200 * SCALE_FACTOR)}x{int(800 * SCALE_FACTOR)}")
            self.history_window.attributes("-topmost", True)

            self.current_file_index = self.get_log_file_index(box_index) - 1
            self.update_history_graph(box_index, self.current_file_index)

    def update_history_graph(self, box_index, file_index):
        log_entries = self.load_log_files(box_index, file_index)
        times, values = zip(*log_entries) if log_entries else ([], [])

        figure = plt.Figure(figsize=(12 * SCALE_FACTOR, 8 * SCALE_FACTOR), dpi=100)
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
        adcs = []

        # ADC 모듈 확인 및 초기화
        for addr in adc_addresses:
            try:
                adc = Adafruit_ADS1x15.ADS1115(address=addr)
                # ADC가 제대로 작동하는지 간단한 읽기를 시도하여 확인
                adc.read_adc(0, gain=GAIN)
                adcs.append(adc)
                print(f"ADC at address {hex(addr)} initialized successfully.")
            except Exception as e:
                print(f"ADC at address {hex(addr)} is not available: {e}")

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
                    print(f"Channel {box_index} Current: {filtered_value:.6f} mA")
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
        # ADC 데이터를 별도의 스레드에서 비동기적으로 처리
        adc_thread = threading.Thread(target=self.run_async_adc)
        adc_thread.daemon = True
        adc_thread.start()

    def run_async_adc(self):
        # 비동기 이벤트 루프를 스레드 내에서 실행
        asyncio.run(self.read_adc_data())

    def schedule_ui_update(self):
        # UI 업데이트를 메인 스레드에서 주기적으로 수행
        self.root.after(10, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        try:
            # 큐에서 데이터를 가져와서 UI를 업데이트
            while not self.adc_queue.empty():
                box_index = self.adc_queue.get_nowait()
                gas_type = self.gas_types.get(f"analog_box_{box_index}", "ORG")
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

            steps = 10
            interval = 10  # 10ms 간격으로 애니메이션 처리

            # 비동기 애니메이션 처리
            self.animate_step(box_index, 0, steps, prev_value, curr_value, full_scale, alarm_levels, interval)

    def animate_step(self, box_index, step, total_steps, prev_value, curr_value, full_scale, alarm_levels, interval):
        if step >= total_steps:
            self.box_states[box_index]["interpolating"] = False
            return

        interpolated_value = prev_value + (curr_value - prev_value) * (step / total_steps)
        formatted_value = int((interpolated_value - 4) / (20 - 4) * full_scale)
        formatted_value = max(0, min(formatted_value, full_scale))

        pwr_on = interpolated_value >= 1.5

        self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
        self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

        self.update_circle_state([self.box_states[box_index]["alarm1_on"], self.box_states[box_index]["alarm2_on"], pwr_on, False], box_index=box_index)

        if pwr_on:
            self.update_segment_display(str(int(formatted_value)).zfill(4), self.box_frames[box_index][1], blink=False, box_index=box_index)
        else:
            self.update_segment_display("    ", self.box_frames[box_index][1], blink=False, box_index=box_index)

        milliamp_text = f"{interpolated_value:.1f} mA" if pwr_on else "PWR OFF"
        milliamp_color = "#00ff00" if pwr_on else "#ff0000"
        self.box_states[box_index]["milliamp_var"].set(milliamp_text)
        box_canvas = self.box_frames[box_index][1]
        box_canvas.itemconfig(self.box_states[box_index]["milliamp_text_id"], text=milliamp_text, fill=milliamp_color)

        # 비동기적으로 다음 단계 호출
        self.root.after(interval, self.animate_step, box_index, step + 1, total_steps, prev_value, curr_value, full_scale, alarm_levels, interval)

    def blink_alarm(self, box_index, is_second_alarm):
        def toggle_color():
            with self.box_states[box_index]["blink_lock"]:
                if is_second_alarm:
                    # AL2 조건: AL1은 멈추고 AL2가 깜빡여야 함
                    self.update_circle_state([True, self.box_states[box_index]["blink_state"], True, False], box_index=box_index)
                else:
                    # AL1 조건: AL1이 깜빡여야 함
                    self.update_circle_state([self.box_states[box_index]["blink_state"], False, True, False], box_index=box_index)

                self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

                # 세그먼트 디스플레이는 깜빡임 없이 유지
                if self.box_states[box_index]["current_value"] is not None:
                    self.update_segment_display(str(self.box_states[box_index]["current_value"]).zfill(4), self.box_frames[box_index][1], blink=False, box_index=box_index)

                # 다음 깜빡임 스케줄링
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
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["analog_gas_types"], alarm_callback=lambda active, box_id: print(f"Alarm {'Active' if active else 'Inactive'} on Box {box_id}"))

    root.mainloop()
