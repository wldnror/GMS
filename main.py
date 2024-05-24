from tkinter import Tk, Label, Entry, Button, StringVar, Frame, Canvas, DISABLED, NORMAL
import time
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException
from rich.console import Console
import netifaces
from PIL import Image, ImageTk

# 세그먼트 표시 매핑
SEGMENTS = {
    '0': '1111110',
    '1': '0110000',
    '2': '1101101',
    '3': '1111001',
    '4': '0110011',
    '5': '1011011',
    '6': '1011111',
    '7': '1110000',
    '8': '1111111',
    '9': '1111011',
    'E': '1001111',  # a, f, e, g, d
    '-': '0000001',  # g
    ' ': '0000000'  # 모든 세그먼트 꺼짐
}

# Bit to segment mapping
BIT_TO_SEGMENT = {
    0: 'E-10',  # E-10
    1: 'E-22',  # E-22
    2: 'E-12',  # E-12
    3: 'E-23'  # E-23
}

class IPInputGUI:
    def __init__(self, root, num_boxes=1):
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

        self.blink_state = False  # 깜빡임 상태 추가
        self.blinking_error = False  # 에러 상태에 따른 깜빡임 제어 변수 추가
        self.previous_value_40011 = None  # 이전 값을 저장하기 위한 변수 추가

        self.box_frame = Frame(self.root)
        self.box_frame.pack()

        self.row_frames = []  # 각 행의 프레임을 저장할 리스트
        self.box_frames = []  # UI 상자를 저장할 리스트

        for _ in range(num_boxes):
            self.create_custom_box()

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

        action_button = Button(frame, text="🔗", command=lambda i=index: self.toggle_connection(i), width=1, height=1, bd=0,
                               highlightthickness=0, borderwidth=0, relief='flat')
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

    def create_custom_box(self):
        i = len(self.box_frames)
        row = i // 7
        col = i % 7

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.pack()
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame)
        box_frame.pack(side='left', padx=10, pady=10)

        box_canvas = Canvas(box_frame, width=170, height=340)
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, 170, 215, fill='grey', outline='grey')
        box_canvas.create_rectangle(0, 215, 170, 340, fill='black', outline='black')

        self.create_segment_display(box_canvas)  # 세그먼트 디스플레이 생성
        self.update_segment_display("0000", box_canvas)

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
        box_canvas.create_text(87, 295, text="GMS-1000", font=("Helvetica", 20, "bold"), fill="#cccccc", anchor="center")

        # 상자 맨 아래에 "GDS ENGINEERING CO.,LTD" 글자 추가
        box_canvas.create_text(87, 328, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 10, "bold"), fill="#cccccc",
                               anchor="center")

        # 40011 값을 시각적으로 표시할 막대 추가
        bar_canvas = Canvas(box_canvas, width=131, height=5, bg="white", highlightthickness=0)
        bar_canvas.place(x=23, y=84)  # 막대를 상자 안의 원하는 위치에 배치

        # 전체 그라데이션 막대를 생성
        gradient_bar = self.create_gradient_bar(131, 5)
        bar_image = ImageTk.PhotoImage(gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append((box_frame, box_canvas, circle_items, bar_canvas, bar_image, bar_item))

    def update_circle_state(self, states, box_index=0):
        """
        동그라미의 상태를 업데이트하는 함수.
        states는 동그라미가 켜져 있는지 여부를 나타내는 리스트.
        """
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

    def create_segment_display(self, box_canvas):
        segment_canvas = Canvas(box_canvas, width=131, height=60, bg='#000000', highlightthickness=0)
        segment_canvas.place(x=23, y=24)  # 상단에 위치

        segment_items = []
        for i in range(4):
            x_offset = i * 29 + 14
            y_offset = i * 20
            segments = [
                # 상단 (4만큼 아래로 이동, 두께 10% 감소)
                segment_canvas.create_polygon(4 + x_offset, 11.2, 12 + x_offset, 11.2, 16 + x_offset, 13.6,
                                              12 + x_offset,
                                              16, 4 + x_offset, 16, 0 + x_offset, 13.6, fill='#424242',
                                              tags=f'segment_{i}_a'),

                # 상단-오른쪽 (세로 열, 두께 감소, 3만큼 아래로 이동)
                segment_canvas.create_polygon(16 + x_offset, 15, 17.6 + x_offset, 17.4, 17.6 + x_offset, 27.4,
                                              16 + x_offset,
                                              29.4, 14.4 + x_offset, 27.4, 14.4 + x_offset, 17.4, fill='#424242',
                                              tags=f'segment_{i}_b'),

                # 하단-오른쪽 (세로 열, 두께 감소, 1만큼 위로 이동)
                segment_canvas.create_polygon(16 + x_offset, 31, 17.6 + x_offset, 33.4, 17.6 + x_offset, 43.4,
                                              16 + x_offset,
                                              45.4, 14.4 + x_offset, 43.4, 14.4 + x_offset, 33.4, fill='#424242',
                                              tags=f'segment_{i}_c'),
                # 하단 (7만큼 위로 이동, 두께 10% 감소)
                segment_canvas.create_polygon(4 + x_offset, 43.8, 12 + x_offset, 43.8, 16 + x_offset, 46.2,
                                              12 + x_offset,
                                              48.6, 4 + x_offset, 48.6, 0 + x_offset, 46.2, fill='#424242',
                                              tags=f'segment_{i}_d'),

                # 하단-왼쪽 (세로 열, 두께 감소, 1만큼 위로 이동)
                segment_canvas.create_polygon(0 + x_offset, 31, 1.6 + x_offset, 33.4, 1.6 + x_offset, 43.4,
                                              0 + x_offset,
                                              45.4, -1.6 + x_offset, 43.4, -1.6 + x_offset, 33.4, fill='#424242',
                                              tags=f'segment_{i}_e'),

                # 상단-왼쪽 (세로 열, 두께 감소, 3만큼 아래로 이동)
                segment_canvas.create_polygon(0 + x_offset, 15, 1.6 + x_offset, 17.4, 1.6 + x_offset, 27.4,
                                              0 + x_offset,
                                              29.4, -1.6 + x_offset, 27.4, -1.6 + x_offset, 17.4, fill='#424242',
                                              tags=f'segment_{i}_f'),

                # 중간 (두께 10% 감소, 아래로 8만큼 이동)
                segment_canvas.create_polygon(4 + x_offset, 27.8, 12 + x_offset, 27.8, 16 + x_offset, 30.2,
                                              12 + x_offset,
                                              32.6, 4 + x_offset, 32.6, 0 + x_offset, 30.2, fill='#424242',
                                              tags=f'segment_{i}_g')
            ]
            segment_items.append(segments)

        box_canvas.segment_canvas = segment_canvas
        box_canvas.segment_items = segment_items

    def update_segment_display(self, value, box_canvas, blink=False):
        value = value.zfill(4)  # Ensure the value is 4 characters long, padded with zeros if necessary
        leading_zero = True
        for i, digit in enumerate(value):
            if leading_zero and digit == '0' and i < 3:
                # 앞의 세 자릿수가 0이면 회색으로 설정
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS[digit]
                leading_zero = False

            if blink and self.blink_state:
                segments = SEGMENTS[' ']  # 깜빡임 상태에서는 모든 세그먼트를 끕니다.

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{i}_{chr(97 + j)}', fill=color)

        self.blink_state = not self.blink_state  # 깜빡임 상태 토글

    def create_gradient_bar(self, width, height):
        gradient = Image.new('RGB', (width, height), color=0)
        for i in range(width):
            ratio = i / width
            if ratio < 0.25:
                r = int(0 + (255 * ratio * 4))
                g = 255
                b = 0
            elif ratio < 0.5:
                r = 255
                g = int(255 - (255 * (ratio - 0.25) * 4))
                b = 0
            elif ratio < 0.75:
                r = 255
                g = 0
                b = int(255 * (ratio - 0.5) * 4)
            else:
                r = int(255 - (255 * (ratio - 0.75) * 4))
                g = 0
                b = 255

            for j in range(height):
                gradient.putpixel((i, j), (r, g, b))

        return gradient

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i)
        else:
            threading.Thread(target=self.connect, args=(i,)).start()  # 비동기 연결 시도

    def connect(self, i):
        ip = self.ip_vars[i].get()
        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502)
            if connect_to_server(ip, client):
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
            self.update_segment_display("0000", self.box_frames[i][1])  # 연결 해제 시 세그먼트 디스플레이 초기화

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
                    elif bit_6_on:
                        blink_state_middle = not blink_state_middle
                        top_blink = False
                        middle_fixed = True
                        middle_blink = blink_state_middle
                    else:
                        top_blink = False
                        middle_blink = False
                        middle_fixed = True

                    # 동그라미 상태 업데이트
                    self.update_circle_state([top_blink, middle_blink, middle_fixed, False], box_index=box_index)

                if not result_40005.isError():
                    value_40005 = result_40005.registers[0]

                    # 40008에 bit 0~3 신호가 없을 때 40005 표시
                    if not result_40007.isError():
                        value_40007 = result_40007.registers[0]

                        # 40007 레지스터의 bit 0, 1, 2, 3 상태 확인
                        bits = [bool(value_40007 & (1 << n)) for n in range(4)]

                        # 40007에 신호가 없으면 40005 값을 세그먼트 디스플레이에 표시
                        if not any(bits):
                            formatted_value = f"{value_40005:04d}"
                            self.update_segment_display(formatted_value, self.box_frames[box_index][1])
                        else:
                            segments_to_display = [BIT_TO_SEGMENT[n] if bit else ' ' for n, bit in enumerate(bits)]
                            error_display = ''.join(segments_to_display)
                            # 세그먼트 디스플레이 업데이트
                            if 'E' in error_display:  # 'E'가 포함된 에러 신호일 경우 깜빡이도록 설정
                                self.blinking_error = True
                                self.update_segment_display(error_display, self.box_frames[box_index][1], blink=True)
                            else:
                                self.blinking_error = False
                                self.update_segment_display(error_display, self.box_frames[box_index][1])
                    else:
                        self.console.print(f"Error from {ip}: {result_40007}")
                else:
                    self.console.print(f"Error from {ip}: {result_40005}")

                if not result_40011.isError():
                    value_40011 = result_40011.registers[0]

                time.sleep(0.2)  # 200ms 간격으로 데이터 읽기 및 LED 깜빡이기

            except ConnectionException:
                self.console.print(f"Connection to {ip} lost. Attempting to reconnect...")
                if connect_to_server(ip, client):
                    self.console.print(f"Reconnected to {ip}")
                else:
                    self.console.print(f"Failed to reconnect to {ip}. Exiting thread.")
                    stop_flag.set()  # 재연결 실패 시 스레드 종료
                    break

def connect_to_server(ip, client):
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

if __name__ == "__main__":
    root = Tk()
    num_boxes = 14  # 원하는 박스 수를 설정하세요.
    ip_input_gui = IPInputGUI(root, num_boxes=num_boxes)

    root.mainloop()

    for _, client in ip_input_gui.clients.items():
        client.close()
