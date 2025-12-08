import json
import os
import time
from tkinter import Frame, Canvas, StringVar, Entry, Button, Tk, Label
import threading
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from rich.console import Console
from PIL import Image, ImageTk

# 외부 파일에서 임포트 (가정)
from common import SEGMENTS, BIT_TO_SEGMENT, create_segment_display, create_gradient_bar
from virtual_keyboard import VirtualKeyboard

import queue

SCALE_FACTOR = 1.65


def sx(x: float) -> int:
    """가로 방향 스케일 헬퍼"""
    return int(x * SCALE_FACTOR)


def sy(y: float) -> int:
    """세로 방향 스케일 헬퍼 (지금은 동일 스케일)"""
    return int(y * SCALE_FACTOR)


class ModbusUI:
    SETTINGS_FILE = "modbus_settings.json"

    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG":    (sx(115), sy(100)),
        "ARF-T":  (sx(107), sy(100)),
        "HMDS":   (sx(110), sy(100)),
        "HC-100": (sx(104), sy(100))
    }

    # 램프 색상 상수
    LAMP_COLORS_ON = ['red', 'red', 'green', 'yellow']
    LAMP_COLORS_OFF = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']

    def __init__(self, parent, num_boxes, gas_types, alarm_callback):
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.virtual_keyboard = VirtualKeyboard(parent)

        self.ip_vars = [StringVar() for _ in range(num_boxes)]
        self.entries = []
        self.action_buttons = []

        self.clients = {}
        self.connected_clients = {}
        self.stop_flags = {}

        self.data_queue = queue.Queue()
        self.ui_update_queue = queue.Queue()
        self.console = Console()

        self.box_states = []
        self.box_frames = []
        self.box_data = []

        # 공통 Bar 이미지
        self.gradient_bar = create_gradient_bar(sx(120), sy(5))
        self.gas_types = gas_types

        # 연결 끊김 관련 관리
        self.disconnection_counts = [0] * num_boxes
        self.disconnection_labels = [None] * num_boxes
        self.auto_reconnect_failed = [False] * num_boxes
        self.reconnect_attempt_labels = [None] * num_boxes

        self.load_ip_settings(num_boxes)

        # 이미지 로드
        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, "img/on.png")
        disconnect_image_path = os.path.join(script_dir, "img/off.png")

        self.connect_image = self.load_image(connect_image_path, (sx(50), sy(70)))
        self.disconnect_image = self.load_image(disconnect_image_path, (sx(50), sy(70)))

        # 박스 생성
        for i in range(num_boxes):
            self.create_modbus_box(i)

        # 통신/깜빡임 간격
        self.communication_interval = 0.2
        self.blink_interval = int(self.communication_interval * 1000)
        self.alarm_blink_interval = 1000

        # 백그라운드 스레드 / UI 업데이트 루프 시작
        self.start_data_processing_thread()
        self.schedule_ui_update()

        self.parent.bind("<Button-1>", self.check_click)

    # -------------------------
    # 일반 유틸 / 설정 관련
    # -------------------------

    def load_ip_settings(self, num_boxes):
        """settings 파일에서 IP 목록을 읽어서 self.ip_vars에 저장"""
        if os.path.exists(self.SETTINGS_FILE):
            with open(self.SETTINGS_FILE, 'r') as file:
                ip_settings = json.load(file)
                for i in range(min(num_boxes, len(ip_settings))):
                    self.ip_vars[i].set(ip_settings[i])

    def save_ip_settings(self):
        """IP 리스트를 json으로 저장"""
        ip_settings = [ip_var.get() for ip_var in self.ip_vars]
        with open(self.SETTINGS_FILE, 'w') as file:
            json.dump(ip_settings, file)

    def load_image(self, path, size):
        img = Image.open(path).convert("RGBA")
        img.thumbnail(size, Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    # -------------------------
    # IP 입력 / 버튼 / 키보드
    # -------------------------

    def add_ip_row(self, frame, ip_var, index):
        """IP 입력부분, 입력 상자(Entry) + 연결버튼"""

        entry_border = Frame(frame, bg="#4a4a4a", bd=1, relief='solid')
        entry_border.grid(row=0, column=0, padx=(0, 0), pady=5)

        entry = Entry(
            entry_border,
            textvariable=ip_var,
            width=int(7 * SCALE_FACTOR),
            highlightthickness=0,
            bd=0,
            relief='flat',
            bg="#2e2e2e",
            fg="white",
            insertbackground="white",
            font=("Helvetica", int(10 * SCALE_FACTOR)),
            justify='center'
        )
        entry.pack(padx=2, pady=3)

        placeholder_text = f"{index + 1}. IP를 입력해주세요."
        if not ip_var.get():
            entry.insert(0, placeholder_text)
            entry.config(fg="#a9a9a9")
        else:
            entry.config(fg="white")

        def on_focus_in(event, e=entry, p=placeholder_text):
            if e['state'] == 'normal':
                if e.get() == p:
                    e.delete(0, "end")
                    e.config(fg="white")
                entry_border.config(bg="#1e90ff")
                e.config(bg="#3a3a3a")

        def on_focus_out(event, e=entry, p=placeholder_text):
            if e['state'] == 'normal':
                if not e.get():
                    e.insert(0, p)
                    e.config(fg="#a9a9a9")
                entry_border.config(bg="#4a4a4a")
                e.config(bg="#2e2e2e")

        def on_entry_click(event, e=entry, p=placeholder_text):
            if e['state'] == 'normal':
                on_focus_in(event, e, p)
                self.show_virtual_keyboard(e)

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Button-1>", on_entry_click)

        action_button = Button(
            frame,
            image=self.connect_image,
            command=lambda i=index: self.toggle_connection(i),
            width=sx(60),
            height=sy(40),
            bd=0,
            highlightthickness=0,
            borderwidth=0,
            relief='flat',
            bg='black',
            activebackground='black',
            cursor="hand2"
        )
        action_button.grid(row=0, column=1)

        self.action_buttons.append(action_button)
        self.entries.append(entry)

    def show_virtual_keyboard(self, entry):
        """터치스크린용 가상 키보드"""
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    # -------------------------
    # 박스 UI 생성 / 갱신
    # -------------------------

    def create_modbus_box(self, index):
        """아날로그박스(캔버스+테두리+IP입력+알람램프 등) 생성"""

        box_frame = Frame(self.parent, highlightthickness=7)
        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(
            inner_frame,
            width=sx(150),
            height=sy(300),
            highlightthickness=sx(3),
            highlightbackground="#000000",
            highlightcolor="#000000",
            bg="#1e1e1e"
        )
        box_canvas.pack()

        # 윗부분 회색, 아랫부분 검정 영역
        box_canvas.create_rectangle(
            0, 0, sx(160), sy(200),
            fill='grey', outline='grey', tags='border'
        )
        box_canvas.create_rectangle(
            0, sy(200), sx(260), sy(310),
            fill='black', outline='grey', tags='border'
        )

        # 세그먼트 디스플레이 생성
        create_segment_display(box_canvas)

        gas_key = self.gas_types.get(f"modbus_box_{index}", "ORG")
        gas_type_var = StringVar(value=gas_key)

        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,
            "pwr_blink_state": False,
            "pwr_blinking": False,
            "gas_type_var": gas_type_var,
            "gas_type_text_id": None,
            "full_scale": self.GAS_FULL_SCALE[gas_key],
            "alarm1_on": False,
            "alarm2_on": False,
            "alarm1_blinking": False,
            "alarm2_blinking": False,
            "alarm_border_blink": False,
            "border_blink_state": False,
            "gms1000_text_id": None
        })

        # Box 안쪽 IP 입력+버튼 컨트롤
        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=sx(10), y=sy(210))

        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        # DC/재연결 라벨
        disconnection_label = Label(
            control_frame,
            text=f"DC: {self.disconnection_counts[index]}",
            fg="white",
            bg="black",
            font=("Helvetica", int(10 * SCALE_FACTOR))
        )
        disconnection_label.grid(row=1, column=0, columnspan=2, pady=(2, 0))
        self.disconnection_labels[index] = disconnection_label

        reconnect_label = Label(
            control_frame,
            text="Reconnect: 0/5",
            fg="yellow",
            bg="black",
            font=("Helvetica", int(10 * SCALE_FACTOR))
        )
        reconnect_label.grid(row=2, column=0, columnspan=2, pady=(2, 0))
        self.reconnect_attempt_labels[index] = reconnect_label

        # 시작 시 라벨 숨김
        disconnection_label.grid_remove()
        reconnect_label.grid_remove()

        # -----------------------------
        # AL1, AL2, PWR, FUT 원(램프)
        # 👉 기존 코드 좌표 그대로
        # -----------------------------
        circle_al1 = box_canvas.create_oval(
            sx(77) - sx(20), sy(200) - sy(32),
            sx(87) - sx(20), sy(190) - sy(32)
        )
        box_canvas.create_text(
            sx(95) - sx(25), sy(222) - sy(40),
            text="AL1",
            fill="#cccccc",
            anchor="e"
        )

        circle_al2 = box_canvas.create_oval(
            sx(133) - sx(30), sy(200) - sy(32),
            sx(123) - sx(30), sy(190) - sy(32)
        )
        box_canvas.create_text(
            sx(140) - sx(35), sy(222) - sy(40),
            text="AL2",
            fill="#cccccc",
            anchor="e"
        )

        circle_pwr = box_canvas.create_oval(
            sx(30) - sx(10), sy(200) - sy(32),
            sx(40) - sx(10), sy(190) - sy(32)
        )
        box_canvas.create_text(
            sx(35) - sx(10), sy(222) - sy(40),
            text="PWR",
            fill="#cccccc",
            anchor="center"
        )

        circle_fut = box_canvas.create_oval(
            sx(171) - sx(40), sy(200) - sy(32),
            sx(181) - sx(40), sy(190) - sy(32)
        )
        box_canvas.create_text(
            sx(175) - sx(40), sy(217) - sy(40),
            text="FUT",
            fill="#cccccc",
            anchor="n"
        )

        # GAS 타입 표시
        gas_pos = self.GAS_TYPE_POSITIONS[gas_type_var.get()]
        gas_type_text_id = box_canvas.create_text(
            *gas_pos,
            text=gas_type_var.get(),
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )
        self.box_states[index]["gas_type_text_id"] = gas_type_text_id

        # GMS-1000 표시 (하단)
        gms1000_text_id = box_canvas.create_text(
            sx(80),
            sy(270),
            text="GMS-1000",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )
        self.box_states[index]["gms1000_text_id"] = gms1000_text_id

        box_canvas.create_text(
            sx(80),
            sy(295),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica", int(7 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )

        # Bar (그래프)
        bar_canvas = Canvas(
            box_canvas,
            width=sx(120),
            height=sy(5),
            bg="white",
            highlightthickness=0
        )
        bar_canvas.place(x=sx(18.5), y=sy(75))

        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append(box_frame)
        self.box_data.append((box_canvas,
                              [circle_al1, circle_al2, circle_pwr, circle_fut],
                              bar_canvas, bar_image, bar_item))

        # 초기 상태: Bar 숨김 + 알람 OFF
        self.show_bar(index, show=False)
        self.update_circle_state([False, False, False, False], box_index=index)

    def update_full_scale(self, gas_type_var, box_index):
        """GAS 타입 바뀌면 Full Scale 갱신 + 위치/텍스트 갱신"""
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_data[box_index][0]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]["gas_type_text_id"], *position)
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

    # -------------------------
    # 램프/세그먼트/Bar 업데이트
    # -------------------------

    def update_circle_state(self, states, box_index=0):
        """AL1, AL2, PWR, FUT 램프 색상 업데이트"""
        box_canvas, circle_items, _, _, _ = self.box_data[box_index]
        for i, state in enumerate(states):
            color = self.LAMP_COLORS_ON[i] if state else self.LAMP_COLORS_OFF[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"modbus_{box_index}")

    def update_segment_display(self, value, box_index=0, blink=False):
        """세그먼트 디스플레이 (4자리)"""
        box_canvas = self.box_data[box_index][0]
        value = value.zfill(4)
        prev_val = self.box_states[box_index]["previous_segment_display"]

        if value != prev_val:
            self.box_states[box_index]["previous_segment_display"] = value

        leading_zero = True
        for idx, digit in enumerate(value):
            if leading_zero and digit == '0' and idx < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS.get(digit, SEGMENTS[' '])
                leading_zero = False

            if blink and self.box_states[box_index]["blink_state"]:
                segments = SEGMENTS[' ']

            for j, seg_on in enumerate(segments):
                color = '#fc0c0c' if seg_on == '1' else '#424242'
                segment_tag = f'segment_{idx}_{chr(97 + j)}'
                if box_canvas.segment_canvas.find_withtag(segment_tag):
                    box_canvas.segment_canvas.itemconfig(segment_tag, fill=color)

        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def update_bar(self, value, box_index):
        """Bar 그래프 업데이트"""
        _, _, bar_canvas, _, bar_item = self.box_data[box_index]
        percentage = value / 100.0
        bar_length = int(153 * SCALE_FACTOR * percentage)
        cropped_image = self.gradient_bar.crop((0, 0, bar_length, sy(5)))
        bar_image = ImageTk.PhotoImage(cropped_image)
        bar_canvas.itemconfig(bar_item, image=bar_image)
        bar_canvas.bar_image = bar_image  # GC 방지

    def show_bar(self, box_index, show):
        """Bar 숨김/표시"""
        bar_canvas = self.box_data[box_index][2]
        bar_item = self.box_data[box_index][4]
        bar_canvas.itemconfig(bar_item, state='normal' if show else 'hidden')

    # -------------------------
    # 연결/해제 / 끊김 / 재연결
    # -------------------------

    def toggle_connection(self, i):
        """연결/해제 토글"""
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i, manual=True)
        else:
            threading.Thread(target=self.connect, args=(i,), daemon=True).start()

    def connect(self, i):
        ip = self.ip_vars[i].get()

        if self.auto_reconnect_failed[i]:
            self.disconnection_counts[i] = 0
            self.disconnection_labels[i].config(text="DC: 0")
            self.auto_reconnect_failed[i] = False

        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502, timeout=3)
            if self.connect_to_server(ip, client):
                stop_flag = threading.Event()
                self.stop_flags[ip] = stop_flag
                self.clients[ip] = client

                t = threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, i),
                    daemon=True
                )
                self.connected_clients[ip] = t
                t.start()
                self.console.print(f"Started data thread for {ip}")

                box_canvas = self.box_data[i][0]
                gms1000_id = self.box_states[i]["gms1000_text_id"]
                box_canvas.itemconfig(gms1000_id, state='hidden')

                self.disconnection_labels[i].grid()
                self.reconnect_attempt_labels[i].grid()

                self.parent.after(
                    0,
                    lambda idx=i: self.action_buttons[idx].config(
                        image=self.disconnect_image,
                        relief='flat',
                        borderwidth=0
                    )
                )
                self.parent.after(0, lambda idx=i: self.entries[idx].config(state="disabled"))

                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
                self.blink_pwr(i)
                self.save_ip_settings()

                # Entry 포커스아웃 강제
                self.entries[i].event_generate("<FocusOut>")
            else:
                self.console.print(f"Failed to connect to {ip}")
                self.parent.after(0, lambda idx=i: self.update_circle_state([False, False, False, False], box_index=idx))

    def disconnect(self, i, manual=False):
        """manual=True -> 사용자가 직접 disconnect"""
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            threading.Thread(
                target=self.disconnect_client,
                args=(ip, i, manual),
                daemon=True
            ).start()

    def disconnect_client(self, ip, i, manual=False):
        """실제 해제 로직"""
        self.stop_flags[ip].set()
        self.connected_clients[ip].join(timeout=5)

        if self.connected_clients[ip].is_alive():
            self.console.print(f"Thread for {ip} did not terminate in time.")

        self.clients[ip].close()
        self.console.print(f"Disconnected from {ip}")
        self.cleanup_client(ip)

        self.parent.after(0, lambda idx=i: self.reset_ui_elements(idx))
        self.parent.after(
            0,
            lambda idx=i: self.action_buttons[idx].config(
                image=self.connect_image,
                relief='flat',
                borderwidth=0
            )
        )
        self.parent.after(0, lambda idx=i: self.entries[idx].config(state="normal"))
        self.parent.after(0, lambda idx=i: self.box_frames[idx].config(highlightthickness=1))

        self.save_ip_settings()

        if manual:
            box_canvas = self.box_data[i][0]
            gms1000_id = self.box_states[i]["gms1000_text_id"]
            box_canvas.itemconfig(gms1000_id, state='normal')
            self.disconnection_labels[i].grid_remove()
            self.reconnect_attempt_labels[i].grid_remove()

    def reset_ui_elements(self, box_index):
        """AL1/AL2/PWR/FUT=OFF, 세그먼트=공백, 바=OFF"""
        self.update_circle_state([False, False, False, False], box_index=box_index)
        self.update_segment_display("    ", box_index=box_index)
        self.show_bar(box_index, show=False)
        self.console.print(f"Reset UI elements for box {box_index}")

    def cleanup_client(self, ip):
        """내부 dict들 정리"""
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

    def connect_to_server(self, ip, client):
        """여러번 시도해서 연결"""
        retries = 5
        for attempt in range(retries):
            if client.connect():
                self.console.print(f"Connected to the Modbus server at {ip}")
                return True
            self.console.print(f"Connection attempt {attempt + 1} to {ip} failed. Retrying in 2 seconds...")
            time.sleep(2)
        return False

    # -------------------------
    # Modbus 데이터 읽기 / 큐 처리
    # -------------------------

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        """주기적으로 holding register 읽어서 큐에 넣고, 끊김 발생하면 reconnect"""
        start_address = 40001 - 1
        num_registers = 11

        while not stop_flag.is_set():
            try:
                if client is None or not client.is_socket_open():
                    raise ConnectionException("Socket is closed")

                response = client.read_holding_registers(start_address, num_registers)
                if response.isError():
                    raise ModbusIOException(f"Error reading from {ip}, address 40001~40011")

                raw_regs = response.registers
                value_40001 = raw_regs[0]
                value_40005 = raw_regs[4]
                value_40007 = raw_regs[7]
                value_40011 = raw_regs[10]

                bit_6_on = bool(value_40001 & (1 << 6))
                bit_7_on = bool(value_40001 & (1 << 7))

                self.box_states[box_index]["alarm1_on"] = bit_6_on
                self.box_states[box_index]["alarm2_on"] = bit_7_on
                self.ui_update_queue.put(('alarm_check', box_index))

                bits = [bool(value_40007 & (1 << n)) for n in range(4)]
                if not any(bits):
                    formatted_value = f"{value_40005}"
                    self.data_queue.put((box_index, formatted_value, False))
                else:
                    error_display = ""
                    for bit_index, bit_flag in enumerate(bits):
                        if bit_flag:
                            error_display = BIT_TO_SEGMENT[bit_index]
                            break
                    error_display = error_display.ljust(4)
                    if 'E' in error_display:
                        self.box_states[box_index]["blinking_error"] = True
                        self.data_queue.put((box_index, error_display, True))
                        self.ui_update_queue.put(
                            ('circle_state', box_index, [False, False, True, self.box_states[box_index]["blink_state"]])
                        )
                    else:
                        self.box_states[box_index]["blinking_error"] = False
                        self.data_queue.put((box_index, error_display, False))
                        self.ui_update_queue.put(
                            ('circle_state', box_index, [False, False, True, False])
                        )

                self.ui_update_queue.put(('bar', box_index, value_40011))
                time.sleep(self.communication_interval)

            except (ConnectionException, ModbusIOException) as e:
                self.console.print(f"Connection to {ip} lost: {e}")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break
            except Exception as e:
                self.console.print(f"Error reading data from {ip}: {e}")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break

    def start_data_processing_thread(self):
        threading.Thread(target=self.process_data, daemon=True).start()

    def process_data(self):
        """Modbus 데이터를 받아 UI 갱신 큐에 넣음"""
        while True:
            try:
                box_index, value, blink = self.data_queue.get(timeout=1)
                self.ui_update_queue.put(('segment_display', box_index, value, blink))
            except queue.Empty:
                continue

    def schedule_ui_update(self):
        self.parent.after(100, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        """UI 업데이트(알람, 바, 세그먼트 등)"""
        while not self.ui_update_queue.empty():
            item = self.ui_update_queue.get_nowait()
            typ = item[0]

            if typ == 'circle_state':
                _, box_index, states = item
                self.update_circle_state(states, box_index=box_index)
            elif typ == 'bar':
                _, box_index, value = item
                self.update_bar(value, box_index)
            elif typ == 'segment_display':
                _, box_index, value, blink = item
                self.update_segment_display(value, box_index=box_index, blink=blink)
            elif typ == 'alarm_check':
                _, box_index = item
                self.check_alarms(box_index)

        self.schedule_ui_update()

    # -------------------------
    # 끊김 / 재연결 / 알람 처리
    # -------------------------

    def check_click(self, event):
        pass

    def handle_disconnection(self, box_index):
        """연결 끊겼을 때 처리"""
        self.disconnection_counts[box_index] += 1
        self.disconnection_labels[box_index].config(
            text=f"DC: {self.disconnection_counts[box_index]}"
        )

        self.ui_update_queue.put(('circle_state', box_index, [False, False, False, False]))
        self.ui_update_queue.put(('segment_display', box_index, "    ", False))
        self.ui_update_queue.put(('bar', box_index, 0))

        self.parent.after(
            0,
            lambda idx=box_index: self.action_buttons[idx].config(
                image=self.connect_image,
                relief='flat',
                borderwidth=0
            )
        )
        self.parent.after(0, lambda idx=box_index: self.entries[idx].config(state="normal"))
        self.parent.after(0, lambda idx=box_index: self.box_frames[idx].config(highlightthickness=1))
        self.parent.after(0, lambda idx=box_index: self.reset_ui_elements(idx))

        self.box_states[box_index]["pwr_blink_state"] = False
        self.box_states[box_index]["pwr_blinking"] = False

        box_canvas = self.box_data[box_index][0]
        circle_items = self.box_data[box_index][1]
        box_canvas.itemconfig(circle_items[2], fill="#e0fbba", outline="#e0fbba")
        self.console.print(f"PWR lamp set to default green for box {box_index} due to disconnection.")

    def reconnect(self, ip, client, stop_flag, box_index):
        """자동 재연결 로직"""
        retries = 0
        max_retries = 5

        while not stop_flag.is_set() and retries < max_retries:
            time.sleep(2)
            self.console.print(f"Attempting to reconnect to {ip} (Attempt {retries + 1}/{max_retries})")

            self.parent.after(
                0,
                lambda idx=box_index, r=retries:
                self.reconnect_attempt_labels[idx].config(text=f"Reconnect: {r + 1}/{max_retries}")
            )

            if client.connect():
                self.console.print(f"Reconnected to the Modbus server at {ip}")
                stop_flag.clear()

                t = threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, box_index),
                    daemon=True
                )
                self.connected_clients[ip] = t
                t.start()

                self.parent.after(
                    0,
                    lambda idx=box_index: self.action_buttons[idx].config(
                        image=self.disconnect_image,
                        relief='flat',
                        borderwidth=0
                    )
                )
                self.parent.after(0, lambda idx=box_index: self.entries[idx].config(state="disabled"))
                self.parent.after(0, lambda idx=box_index: self.box_frames[idx].config(highlightthickness=0))

                self.ui_update_queue.put(('circle_state', box_index, [False, False, True, False]))
                self.blink_pwr(box_index)
                self.show_bar(box_index, show=True)

                self.parent.after(
                    0,
                    lambda idx=box_index:
                    self.reconnect_attempt_labels[idx].config(text="Reconnect: OK")
                )
                break
            else:
                retries += 1
                self.console.print(f"Reconnect attempt to {ip} failed.")

        if retries >= max_retries:
            self.console.print(f"Failed to reconnect to {ip} after {max_retries} attempts.")
            self.auto_reconnect_failed[box_index] = True
            self.parent.after(
                0,
                lambda idx=box_index:
                self.reconnect_attempt_labels[idx].config(text="Reconnect: Failed")
            )
            self.disconnect_client(ip, box_index, manual=False)

    def blink_pwr(self, box_index):
        """PWR 램프 깜박임"""
        if self.box_states[box_index].get("pwr_blinking", False):
            return

        self.box_states[box_index]["pwr_blinking"] = True

        def toggle_color(idx=box_index):
            state = self.box_states[idx]
            if not state["pwr_blinking"]:
                return

            if self.ip_vars[idx].get() not in self.connected_clients:
                box_canvas = self.box_data[idx][0]
                circle_items = self.box_data[idx][1]
                box_canvas.itemconfig(circle_items[2], fill="#e0fbba", outline="#e0fbba")
                state["pwr_blink_state"] = False
                state["pwr_blinking"] = False
                return

            box_canvas = self.box_data[idx][0]
            circle_items = self.box_data[idx][1]
            if state["pwr_blink_state"]:
                box_canvas.itemconfig(circle_items[2], fill="red", outline="red")
            else:
                box_canvas.itemconfig(circle_items[2], fill="green", outline="green")

            state["pwr_blink_state"] = not state["pwr_blink_state"]
            if self.ip_vars[idx].get() in self.connected_clients:
                self.parent.after(self.blink_interval, toggle_color)

        toggle_color()

    # -------------------------
    # 알람 램프 / 테두리 깜박임
    # -------------------------

    def check_alarms(self, box_index):
        """AL1/AL2 상태 보고 깜박임/테두리 색상 처리"""
        alarm1 = self.box_states[box_index]["alarm1_on"]
        alarm2 = self.box_states[box_index]["alarm2_on"]

        if alarm2:
            self.box_states[box_index]["alarm1_blinking"] = False
            self.box_states[box_index]["alarm2_blinking"] = True
            self.set_alarm_lamp(box_index, alarm1_on=True, blink1=False, alarm2_on=True, blink2=True)
            self.box_states[box_index]["alarm_border_blink"] = True
            self.blink_alarms(box_index)
        elif alarm1:
            self.box_states[box_index]["alarm1_blinking"] = True
            self.box_states[box_index]["alarm2_blinking"] = False
            self.box_states[box_index]["alarm_border_blink"] = True
            self.set_alarm_lamp(box_index, alarm1_on=True, blink1=True, alarm2_on=False, blink2=False)
            self.blink_alarms(box_index)
        else:
            self.box_states[box_index]["alarm1_blinking"] = False
            self.box_states[box_index]["alarm2_blinking"] = False
            self.box_states[box_index]["alarm_border_blink"] = False
            self.set_alarm_lamp(box_index, alarm1_on=False, blink1=False, alarm2_on=False, blink2=False)

            box_canvas = self.box_data[box_index][0]
            box_canvas.config(highlightbackground="#000000")
            self.box_states[box_index]["border_blink_state"] = False

    def set_alarm_lamp(self, box_index, alarm1_on, blink1, alarm2_on, blink2):
        box_canvas, circle_items, *_ = self.box_data[box_index]

        # AL1
        if alarm1_on:
            if blink1:
                box_canvas.itemconfig(circle_items[0], fill="#fdc8c8", outline="#fdc8c8")
            else:
                box_canvas.itemconfig(circle_items[0], fill="red", outline="red")
        else:
            box_canvas.itemconfig(circle_items[0], fill="#fdc8c8", outline="#fdc8c8")

        # AL2
        if alarm2_on:
            if blink2:
                box_canvas.itemconfig(circle_items[1], fill="#fdc8c8", outline="#fdc8c8")
            else:
                box_canvas.itemconfig(circle_items[1], fill="red", outline="red")
        else:
            box_canvas.itemconfig(circle_items[1], fill="#fdc8c8", outline="#fdc8c8")

    def blink_alarms(self, box_index):
        """AL1/AL2 or 테두리 깜박임"""
        state = self.box_states[box_index]
        if not (state["alarm1_blinking"] or state["alarm2_blinking"] or state["alarm_border_blink"]):
            return

        box_canvas, circle_items, *_ = self.box_data[box_index]
        border_state = state["border_blink_state"]
        state["border_blink_state"] = not border_state

        if state["alarm_border_blink"]:
            box_canvas.config(highlightbackground="#000000" if border_state else "#ff0000")

        if state["alarm1_blinking"]:
            fill_now = box_canvas.itemcget(circle_items[0], "fill")
            box_canvas.itemconfig(
                circle_items[0],
                fill="#fdc8c8" if fill_now == "red" else "red",
                outline="#fdc8c8" if fill_now == "red" else "red"
            )

        if state["alarm2_blinking"]:
            fill_now = box_canvas.itemcget(circle_items[1], "fill")
            box_canvas.itemconfig(
                circle_items[1],
                fill="#fdc8c8" if fill_now == "red" else "red",
                outline="#fdc8c8" if fill_now == "red" else "red"
            )

        self.parent.after(self.alarm_blink_interval, lambda idx=box_index: self.blink_alarms(idx))


def main():
    root = Tk()
    root.title("Modbus UI")
    root.geometry("1200x600")
    root.configure(bg="#1e1e1e")

    num_boxes = 4
    gas_types = {
        "modbus_box_0": "ORG",
        "modbus_box_1": "ARF-T",
        "modbus_box_2": "HMDS",
        "modbus_box_3": "HC-100"
    }

    def alarm_callback(active, box_id):
        if active:
            print(f"[Callback] Alarm active in {box_id}")
        else:
            print(f"[Callback] Alarm cleared in {box_id}")

    modbus_ui = ModbusUI(root, num_boxes, gas_types, alarm_callback)

    row, col = 0, 0
    max_col = 2
    for frame in modbus_ui.box_frames:
        frame.grid(row=row, column=col, padx=10, pady=10)
        col += 1
        if col >= max_col:
            col = 0
            row += 1

    root.mainloop()


if __name__ == "__main__":
    main()
