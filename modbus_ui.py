from tkinter import Frame, Canvas, StringVar, DISABLED, NORMAL, Entry, Button, Toplevel
import threading
import time
import queue
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException
from rich.console import Console
from PIL import Image, ImageTk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplcursors  # mplcursors 라이브러리 추가
from common import SEGMENTS, BIT_TO_SEGMENT, create_gradient_bar, create_segment_display, show_history_graph

class ModbusUI:
    def __init__(self, root, num_boxes):
        self.root = root
        self.root.title("GDSENG - 스마트 모니터링 시스템")

        self.ip_vars = []
        self.entries = []
        self.action_buttons = []
        self.clients = {}
        self.connected_clients = {}
        self.stop_flags = {}

        self.data_queue = queue.Queue()
        self.console = Console()

        self.box_states = []
        self.histories = [[] for _ in range(num_boxes)]  # 히스토리 저장을 위한 리스트 초기화
        self.graph_windows = [None for _ in range(num_boxes)]  # 그래프 윈도우 저장을 위한 리스트 초기화

        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=20, pady=20)  # grid로 변경하고 padding 추가

        self.row_frames = []  # 각 행의 프레임을 저장할 리스트
        self.box_frames = []  # UI 상자를 저장할 리스트

        self.gradient_bar = create_gradient_bar(131, 5)  # gradient_bar 초기화

        for _ in range(num_boxes):
            self.create_modbus_box()

        # 모든 동그라미를 꺼는 초기화
        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

    def add_ip_row(self, frame, ip_var, index):
        entry = Entry(frame, textvariable=ip_var, width=11, highlightthickness=0)
        entry.insert(0, f"IP Address {index + 1}")
        entry.bind("<FocusIn>", lambda event, e=entry: self.on_focus_in(e))
        entry.bind("<FocusOut>", lambda event, e=entry: self.on_focus_out(e, f"IP Address {index + 1}"))
        entry.grid(row=0, column=0, padx=(0, 5))  # 입력 필드 배치
        self.entries.append(entry)

        action_button = Button(frame, text="🔗", command=lambda i=index: self.toggle_connection(i), width=1, height=1,
                               bd=0, highlightthickness=0, borderwidth=0, relief='flat')
        action_button.grid(row=0, column=1, padx=(0, 5))  # 버튼 배치
        self.action_buttons.append(action_button)

    def on_focus_in(self, entry):
        if entry.get().startswith("IP Address"):
            entry.delete(0, "end")
            entry.config(fg="black")

    def on_focus_out(self, entry, placeholder):
        if not entry.get():
            entry.insert(0, placeholder)
            entry.config(fg="grey")

    def create_modbus_box(self):
        i = len(self.box_frames)
        row = i // 7
        col = i % 7

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0)  # grid로 변경
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame)
        box_frame.grid(row=0, column=col, padx=10, pady=10)  # grid로 변경

        box_canvas = Canvas(box_frame, width=166, height=336, highlightthickness=3, highlightbackground="#000000",
                            highlightcolor="#000000")
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, 170, 215, fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, 215, 170, 340, fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)  # 세그먼트 디스플레이 생성
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,  # 이전 세그먼트 값 저장
            "last_history_time": None,  # 마지막 히스토리 기록 시간
            "last_history_value": None  # 마지막 히스토리 기록 값
        })
        self.update_segment_display("    ", box_canvas, box_index=i)  # 초기화시 빈 상태로 설정

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=10, y=220)

        ip_var = StringVar()
        self.ip_vars.append(ip_var)

        self.add_ip_row(control_frame, ip_var, i)

        # 동그라미 상태를 저장할 리스트
        circle_items = []

        # Draw small circles in the desired positions (moved to gray section)
        # Left vertical row under the segment display
        circle_items.append(
            box_canvas.create_oval(110, 160, 100, 170))  # Red circle 1
        box_canvas.create_text(75, 183, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(
            box_canvas.create_oval(60, 160, 70, 170))  # Red circle 2
        box_canvas.create_text(117, 183, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(
            box_canvas.create_oval(20, 160, 30, 170))  # Green circle 1
        box_canvas.create_text(25, 183, text="PWR", fill="#cccccc", anchor="center")

        # Right horizontal row under the segment display
        circle_items.append(
            box_canvas.create_oval(141, 160, 151, 170))  # Yellow circle 1
        box_canvas.create_text(148, 175, text="FUT", fill="#cccccc", anchor="n")

        # 상자 세그먼트 아래에 "가스명" 글자 추가
        box_canvas.create_text(129, 105, text="ORG", font=("Helvetica", 20, "bold"), fill="#cccccc", anchor="center")

        # 상자 맨 아래에 "GDS SMS" 글자 추가
        box_canvas.create_text(87, 295, text="GMS-1000", font=("Helvetica", 20, "bold"), fill="#cccccc",
                               anchor="center")

        # 상자 맨 아래에 "GDS ENGINEERING CO.,LTD" 글자 추가
        box_canvas.create_text(87, 328, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 10, "bold"), fill="#cccccc",
                               anchor="center")

        # 40011 값을 시각적으로 표시할 막대 추가
        bar_canvas = Canvas(box_canvas, width=131, height=5, bg="white", highlightthickness=0)
        bar_canvas.place(x=23, y=84)  # 막대를 상자 안의 원하는 위치에 배치

        # 전체 그라데이션 막대를 생성
        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append((box_frame, box_canvas, circle_items, bar_canvas, bar_image, bar_item))

        # 무지개 바 초기 숨김 처리
        self.show_bar(i, show=False)

        # 세그먼트 클릭 시 히스토리를 그래프로 보여주는 이벤트 추가
        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=i: show_history_graph(self.root, i, self.histories, self.graph_windows))

    def update_circle_state(self, states, box_index=0):
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        if states[0]:  # Red top-left
            outline_color = outline_colors[0]
        elif states[1]:  # Red top-right
            outline_color = outline_colors[1]
        elif states[3]:  # Yellow bottom-right
            outline_color = outline_colors[3]
        else:  # Default grey outline
            outline_color = outline_color_off

        # 박스 테두리 업데이트
        box_canvas.config(highlightbackground=outline_color)

    def update_segment_display(self, value, box_canvas, blink=False, box_index=0):
        value = value.zfill(4)  # Ensure the value is 4 characters long, padded with zeros if necessary
        leading_zero = True
        blink_state = self.box_states[box_index]["blink_state"]
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:  # 값이 변했을 때만 기록
            self.record_history(box_index, value)
            self.box_states[box_index]["previous_segment_display"] = value

        for i, digit in enumerate(value):
            if leading_zero and digit == '0' and i < 3:
                # 앞의 세 자릿수가 0이면 회색으로 설정
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS[digit]
                leading_zero = False

            if blink and blink_state:
                segments = SEGMENTS[' ']  # 깜빡임 상태에서는 모든 세그먼트를 끕니다.

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{i}_{chr(97 + j)}', fill=color)

        self.box_states[box_index]["blink_state"] = not blink_state  # 깜빡임 상태 토글

    def record_history(self, box_index, value):
        if value.strip():  # 값이 공백이 아닌 경우에만 기록
            last_history_value = self.box_states[box_index]["last_history_value"]
            if value != last_history_value:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                last_value = self.box_states[box_index].get("last_value_40005", 0)
                self.histories[box_index].append((timestamp, value, last_value))
                self.box_states[box_index]["last_history_value"] = value
                if len(self.histories[box_index]) > 100:  # 최대 기록 수를 제한
                    self.histories[box_index].pop(0)

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i)
        else:
            threading.Thread(target=self.connect, args=(i,)).start()  # 비동기 연결 시도

    def connect(self, i):
        ip = self.ip_vars[i].get()
        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502)
            if self.connect_to_server(ip, client):
                stop_flag = threading.Event()
                self.stop_flags[ip] = stop_flag
                self.clients[ip] = client
                self.connected_clients[ip] = threading.Thread(target=self.read_modbus_data,
                                                              args=(ip, client, stop_flag, i))
                self.connected_clients[ip].daemon = True
                self.connected_clients[ip].start()
                self.console.print(f"Started data thread for {ip}")
                self.root.after(0, lambda: self.action_buttons[i].config(text="❌", relief='flat', borderwidth=0))  # 연결 성공 시 버튼을 연결 해제로 변경
                self.root.after(0, lambda: self.entries[i].config(state=DISABLED))  # 연결 성공 시 IP 입력 필드 비활성화
                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)  # 무지개 바 보이기
            else:
                self.console.print(f"Failed to connect to {ip}")

    def disconnect(self, i):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            self.stop_flags[ip].set()  # 스레드 종료 신호 설정
            self.clients[ip].close()
            self.console.print(f"Disconnected from {ip}")
            self.connected_clients[ip].join()  # 스레드가 종료될 때까지 대기
            self.cleanup_client(ip)
            self.ip_vars[i].set('')  # IP 입력 필드를 비웁니다.
            self.action_buttons[i].config(text="🔗", relief='flat', borderwidth=0)  # 연결 해제 시 버튼을 연결로 변경
            self.root.after(0, lambda: self.entries[i].config(state=NORMAL))  # 연결 해제 시 IP 입력 필드 활성화
            self.update_circle_state([False, False, False, False], box_index=i)
            self.update_segment_display("    ", self.box_frames[i][1], box_index=i)  # 연결 해제 시 세그먼트 디스플레이 초기화
            self.show_bar(i, show=False)  # 무지개 바 숨기기

    def cleanup_client(self, ip):
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        blink_state_middle = False
        blink_state_top = False
        while not stop_flag.is_set():
            try:
                address_40001 = 40001 - 1  # Modbus 주소는 0부터 시작하므로 40001의 실제 주소는 40000
                address_40005 = 40005 - 1  # Modbus 주소는 0부터 시작하므로 40005의 실제 주소는 40004
                address_40007 = 40008 - 1  # Modbus 주소는 0부터 시작하므로 40008의 실제 주소는 40007
                address_40011 = 40011 - 1  # Modbus 주소는 0부터 시작하므로 40011의 실제 주소는 40010
                count = 1
                result_40001 = client.read_holding_registers(address_40001, count, unit=1)
                result_40005 = client.read_holding_registers(address_40005, count, unit=1)
                result_40007 = client.read_holding_registers(address_40007, count, unit=1)
                result_40011 = client.read_holding_registers(address_40011, count, unit=1)

                if not result_40001.isError():
                    value_40001 = result_40001.registers[0]

                    # 6번째 비트 및 7번째 비트 상태 확인
                    bit_6_on = bool(value_40001 & (1 << 6))
                    bit_7_on = bool(value_40001 & (1 << 7))

                    if bit_7_on:
                        blink_state_top = not blink_state_top
                        top_blink = blink_state_top
                        middle_fixed = True
                        middle_blink = True
                        self.record_history(box_index, 'A2')
                    elif bit_6_on:
                        blink_state_middle = not blink_state_middle
                        top_blink = False
                        middle_fixed = True
                        middle_blink = blink_state_middle
                        self.record_history(box_index, 'A1')
                    else:
                        top_blink = False
                        middle_blink = False
                        middle_fixed = True

                    # 동그라미 상태 업데이트
                    self.update_circle_state([top_blink, middle_blink, middle_fixed, False], box_index=box_index)

                if not result_40005.isError():
                    value_40005 = result_40005.registers[0]
                    self.box_states[box_index]["last_value_40005"] = value_40005

                    # 40008에 bit 0~3 신호가 없을 때 40005 표시
                    if not result_40007.isError():
                        value_40007 = result_40007.registers[0]

                        # 40007 레지스터의 bit 0, 1, 2, 3 상태 확인
                        bits = [bool(value_40007 & (1 << n)) for n in range(4)]

                        # 40007에 신호가 없으면 40005 값을 세그먼트 디스플레이에 표시
                        if not any(bits):
                            formatted_value = f"{value_40005:04d}"
                            self.update_segment_display(formatted_value, self.box_frames[box_index][1], box_index=box_index)
                        else:
                            error_display = ""
                            for i, bit in enumerate(bits):
                                if bit:
                                    error_display = BIT_TO_SEGMENT[i]
                                    self.record_history(box_index, error_display)
                                    break

                            error_display = error_display.ljust(4)  # 길이를 4로 맞춤

                            # 세그먼트 디스플레이 업데이트
                            if 'E' in error_display:  # 'E'가 포함된 에러 신호일 경우 깜빡이도록 설정
                                self.box_states[box_index]["blinking_error"] = True
                                self.update_segment_display(error_display, self.box_frames[box_index][1], blink=True, box_index=box_index)
                                self.update_circle_state([False, False, True, self.box_states[box_index]["blink_state"]],
                                                         box_index=box_index)  # 노란색 LED 깜빡임
                            else:
                                self.box_states[box_index]["blinking_error"] = False
                                self.update_segment_display(error_display, self.box_frames[box_index][1], box_index=box_index)
                                self.update_circle_state([False, False, True, False], box_index=box_index)  # 노란색 LED 끄기
                    else:
                        self.console.print(f"Error from {ip}: {result_40007}")
                else:
                    self.console.print(f"Error from {ip}: {result_40005}")

                if not result_40011.isError():
                    value_40011 = result_40011.registers[0]
                    self.update_bar(value_40011, self.box_frames[box_index][3], self.box_frames[box_index][5])  # 40011 값에 따라 막대 업데이트

                time.sleep(0.2)  # 200ms 간격으로 데이터 읽기 및 히스토리 기록

            except ConnectionException:
                self.console.print(f"Connection to {ip} lost. Attempting to reconnect...")
                if self.connect_to_server(ip, client):
                    self.console.print(f"Reconnected to {ip}")
                else:
                    self.console.print(f"Failed to reconnect to {ip}. Exiting thread.")
                    stop_flag.set()  # 재연결 실패 시 스레드 종료
                    break

    def update_bar(self, value, bar_canvas, bar_item):
        percentage = value / 100.0
        bar_length = int(131 * percentage)

        # 잘라내어 새로운 이미지를 생성
        cropped_image = self.gradient_bar.crop((0, 0, bar_length, 5))
        bar_image = ImageTk.PhotoImage(cropped_image)
        bar_canvas.itemconfig(bar_item, image=bar_image)
        bar_canvas.bar_image = bar_image  # 이미지가 GC에 의해 수집되지 않도록 참조를 유지

    def show_bar(self, box_index, show):
        bar_canvas, _, bar_item = self.box_frames[box_index][3:6]
        if show:
            bar_canvas.itemconfig(bar_item, state='normal')
        else:
            bar_canvas.itemconfig(bar_item, state='hidden')

    def connect_to_server(self, ip, client):
        retries = 5
        for attempt in range(retries):
            connection = client.connect()
            if connection:
                print(f"Connected to the Modbus server at {ip}")
                return True
            else:
                print(f"Connection attempt {attempt + 1} to {ip} failed. Retrying in 5 seconds...")
                time.sleep(5)
        return False
