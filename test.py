from tkinter import Tk, Label, Entry, Button, StringVar, Frame, Canvas
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
    def __init__(self, root):
        self.root = root
        self.root.title("Modbus Server IPs")

        self.frame = Frame(root)
        self.frame.pack()

        self.ip_vars = []
        self.entries = []
        self.connect_buttons = []
        self.disconnect_buttons = []
        self.clients = {}
        self.connected_clients = {}
        self.stop_flags = {}

        self.add_ip_row()

        self.data_queue = queue.Queue()
        self.console = Console()

        self.blink_state = False  # 깜빡임 상태 추가
        self.blinking_error = False  # 에러 상태에 따른 깜빡임 제어 변수 추가

        self.create_custom_box()  # 세그먼트 디스플레이 생성 전 박스 생성

        self.scanner_thread = threading.Thread(target=self.scan_network)
        self.scanner_thread.daemon = True
        self.scanner_thread.start()

        self.control_frame = Frame(root)
        self.control_frame.pack()

        self.add_button = Button(self.control_frame, text="+", command=self.add_ip_row)
        self.add_button.pack(side="left")
        self.remove_button = Button(self.control_frame, text="-", command=self.remove_ip_row)
        self.remove_button.pack(side="left")

        # 40001 주소의 값을 표시할 레이블 추가
        self.label_40001 = Label(self.root, text="40001: 0000 0000 0000 0000", font=("Courier", 16))
        self.label_40001.pack(pady=10)

        self.label_40008 = Label(self.root, text="40008: 0000 0000 0000 0000", font=("Courier", 16))
        self.label_40008.pack(pady=10)

        # 40011 주소의 값을 표시할 레이블 추가
        self.label_40011 = Label(self.root, text="40011: 0000 0000 0000 0000", font=("Courier", 16))
        self.label_40011.pack(pady=10)

        # 40011 값을 시각적으로 표시할 막대 추가
        self.bar_canvas = Canvas(self.root, width=131, height=7, bg="white", highlightthickness=0)
        self.bar_canvas.place(x=170, y=110)  # x와 y 값을 원하는 위치로 설정

        # 전체 그라데이션 막대를 생성
        self.gradient_bar = self.create_gradient_bar(131)
        self.bar_image = ImageTk.PhotoImage(self.gradient_bar)
        self.bar_item = self.bar_canvas.create_image(0, 0, anchor='nw', image=self.bar_image)

        self.blink_state = False  # 깜빡임 상태 추가
        self.blinking_error = False  # 에러 상태에 따른 깜빡임 제어 변수

    def add_ip_row(self):
        i = len(self.ip_vars)
        ip_var = StringVar()
        self.ip_vars.append(ip_var)

        Label(self.frame, text=f"IP Address {i + 1}:").grid(row=i, column=0)
        entry = Entry(self.frame, textvariable=ip_var)
        entry.grid(row=i, column=1)
        self.entries.append(entry)

        connect_button = Button(self.frame, text="Connect", command=lambda i=i: self.connect(i))
        connect_button.grid(row=i, column=2)
        self.connect_buttons.append(connect_button)

        disconnect_button = Button(self.frame, text="Disconnect", command=lambda i=i: self.disconnect(i))
        disconnect_button.grid(row=i, column=3)
        self.disconnect_buttons.append(disconnect_button)

    def remove_ip_row(self):
        if len(self.ip_vars) > 1:
            i = len(self.ip_vars) - 1
            self.entries[i].grid_remove()
            self.connect_buttons[i].grid_remove()
            self.disconnect_buttons[i].grid_remove()

            self.ip_vars.pop()
            self.entries.pop()
            self.connect_buttons.pop()
            self.disconnect_buttons.pop()

    def create_custom_box(self):
        self.box_canvas = Canvas(self.root, width=170, height=340)
        self.box_canvas.pack()

        self.box_canvas.create_rectangle(0, 0, 170, 215, fill='grey', outline='grey')
        self.box_canvas.create_rectangle(0, 215, 170, 340, fill='black', outline='black')

        self.create_segment_display()  # 세그먼트 디스플레이 생성
        self.update_segment_display("0000")

        # 동그라미 상태를 저장할 리스트
        self.circle_items = []

        # Draw small circles in the desired positions (moved to gray section)
        # Left vertical row under the segment display
        self.circle_items.append(
            self.box_canvas.create_oval(15, 110, 25, 120))  # Red circle 1
        self.box_canvas.create_text(55, 115, text="AL2", fill="#cccccc", anchor="e")

        self.circle_items.append(
            self.box_canvas.create_oval(15, 135, 25, 145))  # Red circle 2
        self.box_canvas.create_text(53, 139.5, text="AL1", fill="#cccccc", anchor="e")

        self.circle_items.append(
            self.box_canvas.create_oval(15, 160, 25, 170))  # Green circle 1
        self.box_canvas.create_text(21, 183, text="PWR", fill="#cccccc", anchor="center")

        # Right horizontal row under the segment display
        self.circle_items.append(
            self.box_canvas.create_oval(78, 160, 88, 170))  # Yellow circle 1
        self.box_canvas.create_text(83.4, 175, text="FUT", fill="#cccccc", anchor="n")

        self.circle_items.append(
            self.box_canvas.create_oval(110, 160, 120, 170))  # Green circle 2
        self.box_canvas.create_text(114.5, 175, text="RY1", fill="#cccccc", anchor="n")

        self.circle_items.append(
            self.box_canvas.create_oval(141, 160, 151, 170))  # Green circle 3
        self.box_canvas.create_text(146.2, 175, text="RY2", fill="#cccccc", anchor="n")

        # 상자 맨 아래에 "GDS ENGINEERING CO.,LTD" 글자 추가
        self.box_canvas.create_text(87, 328, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 11), fill="#cccccc",
                                    anchor="center")

    def update_circle_state(self, states):
        """
        동그라미의 상태를 업데이트하는 함수.
        states는 동그라미가 켜져 있는지 여부를 나타내는 리스트.
        """
        colors_on = ['red', 'red', 'green', 'yellow', 'green', 'green']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf', '#e0fbba', '#e0fbba']

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            self.box_canvas.itemconfig(self.circle_items[i], fill=color, outline=color)

    def create_segment_display(self):
        self.segment_canvas = Canvas(self.box_canvas, width=131, height=60, bg='#000000', highlightthickness=0)
        self.segment_canvas.place(x=23, y=24)  # 상단에 위치

        self.segment_items = []
        for i in range(4):
            x_offset = i * 29 + 14
            y_offset = i * 20
            segments = [
                # 상단 (4만큼 아래로 이동, 두께 10% 감소)
                self.segment_canvas.create_polygon(4 + x_offset, 11.2, 12 + x_offset, 11.2, 16 + x_offset, 13.6,
                                                   12 + x_offset,
                                                   16, 4 + x_offset, 16, 0 + x_offset, 13.6, fill='#424242',
                                                   tags=f'segment_{i}_a'),

                # 상단-오른쪽 (세로 열, 두께 감소, 3만큼 아래로 이동)
                self.segment_canvas.create_polygon(16 + x_offset, 15, 17.6 + x_offset, 17.4, 17.6 + x_offset, 27.4,
                                                   16 + x_offset,
                                                   29.4, 14.4 + x_offset, 27.4, 14.4 + x_offset, 17.4, fill='#424242',
                                                   tags=f'segment_{i}_b'),

                # 하단-오른쪽 (세로 열, 두께 감소, 1만큼 위로 이동)
                self.segment_canvas.create_polygon(16 + x_offset, 31, 17.6 + x_offset, 33.4, 17.6 + x_offset, 43.4,
                                                   16 + x_offset,
                                                   45.4, 14.4 + x_offset, 43.4, 14.4 + x_offset, 33.4, fill='#424242',
                                                   tags=f'segment_{i}_c'),
                # 하단 (7만큼 위로 이동, 두께 10% 감소)
                self.segment_canvas.create_polygon(4 + x_offset, 43.8, 12 + x_offset, 43.8, 16 + x_offset, 46.2,
                                                   12 + x_offset,
                                                   48.6, 4 + x_offset, 48.6, 0 + x_offset, 46.2, fill='#424242',
                                                   tags=f'segment_{i}_d'),

                # 하단-왼쪽 (세로 열, 두께 감소, 1만큼 위로 이동)
                self.segment_canvas.create_polygon(0 + x_offset, 31, 1.6 + x_offset, 33.4, 1.6 + x_offset, 43.4,
                                                   0 + x_offset,
                                                   45.4, -1.6 + x_offset, 43.4, -1.6 + x_offset, 33.4, fill='#424242',
                                                   tags=f'segment_{i}_e'),

                # 상단-왼쪽 (세로 열, 두께 감소, 3만큼 아래로 이동)
                self.segment_canvas.create_polygon(0 + x_offset, 15, 1.6 + x_offset, 17.4, 1.6 + x_offset, 27.4,
                                                   0 + x_offset,
                                                   29.4, -1.6 + x_offset, 27.4, -1.6 + x_offset, 17.4, fill='#424242',
                                                   tags=f'segment_{i}_f'),

                # 중간 (두께 10% 감소, 아래로 8만큼 이동)
                self.segment_canvas.create_polygon(4 + x_offset, 27.8, 12 + x_offset, 27.8, 16 + x_offset, 30.2,
                                                   12 + x_offset,
                                                   32.6, 4 + x_offset, 32.6, 0 + x_offset, 30.2, fill='#424242',
                                                   tags=f'segment_{i}_g')
            ]
            self.segment_items.append(segments)

    def update_segment_display(self, value, blink=False):
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
                self.segment_canvas.itemconfig(f'segment_{i}_{chr(97 + j)}', fill=color)

        self.blink_state = not self.blink_state  # 깜빡임 상태 토글

    def update_label_40001(self, value):
        binary_value = f"{value:016b}"
        formatted_value = f"{binary_value[:4]} {binary_value[4:8]} {binary_value[8:12]} {binary_value[12:]}"
        self.label_40001.config(text=f"40001: {formatted_value}")

    def update_label_40008(self, value):
        binary_value = f"{value:016b}"
        formatted_value = f"{binary_value[:4]} {binary_value[4:8]} {binary_value[8:12]} {binary_value[12:]}"
        self.label_40008.config(text=f"40008: {formatted_value}")

    def update_label_40011(self, value):
        binary_value = f"{value:016b}"
        formatted_value = f"{binary_value[:4]} {binary_value[4:8]} {binary_value[8:12]} {binary_value[12:]}"
        self.label_40011.config(text=f"40011: {formatted_value}")
        # 40011 값을 시각적으로 표시
        percentage = value / 100.0
        bar_length = int(131 * percentage)

        # 잘라내어 새로운 이미지를 생성
        cropped_image = self.gradient_bar.crop((0, 0, bar_length, 20))
        self.bar_image = ImageTk.PhotoImage(cropped_image)
        self.bar_canvas.itemconfig(self.bar_item, image=self.bar_image)

    def create_gradient_bar(self, width):
        gradient = Image.new('RGB', (width, 20), color=0)
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

            for j in range(20):
                gradient.putpixel((i, j), (r, g, b))

        return gradient

    def connect(self, i):
        ip = self.ip_vars[i].get()
        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502)
            if connect_to_server(ip, client):
                stop_flag = threading.Event()
                self.stop_flags[ip] = stop_flag
                self.clients[ip] = client
                self.connected_clients[ip] = threading.Thread(target=self.read_modbus_data,
                                                              args=(ip, client, stop_flag))
                self.connected_clients[ip].daemon = True
                self.connected_clients[ip].start()
                self.console.print(f"Started data thread for {ip}")
                # 통신 성공 시 세 번째 동그라미를 초록색으로 점등
                self.update_circle_state([False, False, True, False, False, False])
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
            # 연결 종료 시 세 번째 동그라미를 끔
            self.update_circle_state([False, False, False, False, False, False])

    def cleanup_client(self, ip):
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

    def read_modbus_data(self, ip, client, stop_flag):
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

                    # 40001 주소 값을 화면에 표시
                    self.update_label_40001(value_40001)

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
                    self.update_circle_state([top_blink, middle_blink, middle_fixed, False, False, False])

                if not result_40005.isError():
                    value_40005 = result_40005.registers[0]

                    # 40008에 bit 0~3 신호가 없을 때 40005 표시
                    if not result_40007.isError():
                        value_40007 = result_40007.registers[0]

                        # 40008 주소 값을 화면에 표시
                        self.update_label_40008(value_40007)

                        # 40007 레지스터의 bit 0, 1, 2, 3 상태 확인
                        bits = [bool(value_40007 & (1 << n)) for n in range(4)]

                        # 40007에 신호가 없으면 40005 값을 세그먼트 디스플레이에 표시
                        if not any(bits):
                            formatted_value = f"{value_40005:04d}"
                            self.update_segment_display(formatted_value)
                        else:
                            segments_to_display = [BIT_TO_SEGMENT[n] if bit else ' ' for n, bit in enumerate(bits)]
                            error_display = ''.join(segments_to_display)
                            # 세그먼트 디스플레이 업데이트
                            if 'E' in error_display:  # 'E'가 포함된 에러 신호일 경우 깜빡이도록 설정
                                self.blinking_error = True
                                self.update_segment_display(error_display, blink=True)
                            else:
                                self.blinking_error = False
                                self.update_segment_display(error_display)
                    else:
                        self.console.print(f"Error from {ip}: {result_40007}")
                else:
                    self.console.print(f"Error from {ip}: {result_40005}")

                if not result_40011.isError():
                    value_40011 = result_40011.registers[0]
                    # 40011 주소 값을 화면에 표시
                    self.update_label_40011(value_40011)

                time.sleep(0.2)  # 200ms 간격으로 데이터 읽기 및 LED 깜빡이기

            except ConnectionException:
                self.console.print(f"Connection to {ip} lost. Attempting to reconnect...")
                if connect_to_server(ip, client):
                    self.console.print(f"Reconnected to {ip}")
                else:
                    self.console.print(f"Failed to reconnect to {ip}. Exiting thread.")
                    stop_flag.set()  # 재연결 실패 시 스레드 종료
                    break

    def scan_network(self):
        gateway_ip, subnet_mask = self.get_network_details()
        ips_to_scan = self.get_ip_range(gateway_ip, subnet_mask)

        while True:
            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = {executor.submit(self.is_device_online, ip): ip for ip in ips_to_scan}
                for future in as_completed(futures):
                    ip = futures[future]
                    if future.result():
                        self.console.print(f"Found device at {ip}, attempting to connect...")
                        self.auto_connect(ip)
            time.sleep(10)

    def get_network_details(self):
        gws = netifaces.gateways()
        try:
            default_gateway = gws['default'][netifaces.AF_INET][0]
            iface = gws['default'][netifaces.AF_INET][1]
            netmask = netifaces.ifaddresses(iface)[netifaces.AF_INET][0]['netmask']
            return default_gateway, netmask
        except KeyError:
            self.console.print("No default gateway found.")
            return None, None

    def get_ip_range(self, gateway_ip, subnet_mask):
        if gateway_ip is None or subnet_mask is None:
            return []
        ip_parts = list(map(int, gateway_ip.split('.')))
        mask_parts = list(map(int, subnet_mask.split('.')))

        network_prefix = [ip_parts[i] & mask_parts[i] for i in range(4)]
        ip_range = []

        for i in range(1, 256):
            for j in range(1, 256):
                if i == ip_parts[2] or j == ip_parts[3]:
                    ip_range.append(f'{network_prefix[0]}.{network_prefix[1]}.{i}.{j}')

        return ip_range

    def is_device_online(self, ip):
        try:
            client = ModbusTcpClient(ip, port=502)
            if client.connect():
                client.close()
                return True
        except Exception as e:
            self.console.print(f"Error checking {ip}: {e}")
        return False

    def auto_connect(self, ip):
        for i in range(len(self.ip_vars)):
            if not self.ip_vars[i].get():
                self.ip_vars[i].set(ip)
                self.connect(i)
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
    ip_input_gui = IPInputGUI(root)

    # 모든 동그라미를 꺼는 예시
    ip_input_gui.update_circle_state([False, False, False, False, False, False])

    root.mainloop()

    for _, client in ip_input_gui.clients.items():
        client.close()

