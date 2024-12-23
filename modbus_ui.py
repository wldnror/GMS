# modbus_ui.py
import json
import os
import time
from tkinter import Frame, Canvas, StringVar, Entry, Button
import threading
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from rich.console import Console
from PIL import Image, ImageTk
from common import SEGMENTS, BIT_TO_SEGMENT, create_segment_display, create_gradient_bar
from virtual_keyboard import VirtualKeyboard
import queue

SCALE_FACTOR = 1.65

class ModbusUI:
    LOGS_PER_FILE = 10
    SETTINGS_FILE = "modbus_settings.json"
    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG":   (int(115 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "ARF-T": (int(107 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "HMDS":  (int(110 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "HC-100":(int(104 * SCALE_FACTOR), int(100 * SCALE_FACTOR))
    }

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
        self.graph_windows = [None for _ in range(num_boxes)]
        self.box_frames = []
        self.box_data = []
        self.gradient_bar = create_gradient_bar(int(120 * SCALE_FACTOR), int(5 * SCALE_FACTOR))
        self.gas_types = gas_types

        self.load_ip_settings(num_boxes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, "img/on.png")
        disconnect_image_path = os.path.join(script_dir, "img/off.png")

        self.connect_image = self.load_image(connect_image_path, (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR)))
        self.disconnect_image = self.load_image(disconnect_image_path, (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR)))

        # ---------------------------------------------------------------------
        #  모든 박스(상자)를 가로(row=0, column=i)로 나란히 배치
        #  만약 2×2 형태로 배치하고 싶다면 row, column 계산을 바꾸시면 됩니다.
        # ---------------------------------------------------------------------
        for i in range(num_boxes):
            self.create_modbus_box(i, row=0, col=i)

        self.communication_interval = 0.2  # 200ms
        self.blink_interval = int(self.communication_interval * 1000)  # PWR 램프 깜빡임 주기

        self.start_data_processing_thread()
        self.schedule_ui_update()
        self.parent.bind("<Button-1>", self.check_click)

    def load_ip_settings(self, num_boxes):
        if os.path.exists(self.SETTINGS_FILE):
            with open(self.SETTINGS_FILE, 'r') as file:
                ip_settings = json.load(file)
                for i in range(min(num_boxes, len(ip_settings))):
                    self.ip_vars[i].set(ip_settings[i])
        else:
            self.ip_vars = [StringVar() for _ in range(num_boxes)]

    def load_image(self, path, size):
        img = Image.open(path).convert("RGBA")
        img.thumbnail(size, Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def create_modbus_box(self, index, row=0, col=0):
        """
        하나의 박스(장치 영역)를 생성하고, grid(row=row, column=col)로 배치.
        """
        # box_frame: 박스 하나를 감싸는 최상위 프레임
        box_frame = Frame(self.parent, highlightthickness=int(3 * SCALE_FACTOR))
        box_frame.grid(row=row, column=col, padx=10, pady=10, sticky="n")  # <-- grid 사용

        # 추후 디스커넥트 시 highlightthickness 수정할 수 있도록 리스트에 저장
        self.box_frames.append(box_frame)

        # inner_frame: 박스 안을 구성하는 서브 프레임 (전체 레이아웃 관리용)
        inner_frame = Frame(box_frame)
        inner_frame.grid(row=0, column=0, sticky="n")

        # box_canvas: 세그먼트, 원형 알람 표시 등을 그릴 Canvas
        box_canvas = Canvas(
            inner_frame,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000000",
            highlightcolor="#000000"
        )
        box_canvas.grid(row=0, column=0, sticky="n")

        # 배경 사각형 그리기
        box_canvas.create_rectangle(
            0, 0,
            int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR),
            fill='grey', outline='grey', tags='border'
        )
        box_canvas.create_rectangle(
            0, int(200 * SCALE_FACTOR),
            int(260 * SCALE_FACTOR), int(310 * SCALE_FACTOR),
            fill='black', outline='grey', tags='border'
        )

        # 세그먼트 디스플레이 생성
        create_segment_display(box_canvas)

        # box_states 초기화
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,
            "pwr_blink_state": False,
            "gas_type_var": StringVar(value=self.gas_types.get(f"modbus_box_{index}", "ORG")),
            "gas_type_text_id": None,
            "full_scale": self.GAS_FULL_SCALE[self.gas_types.get(f"modbus_box_{index}", "ORG")]
        })

        # GAS Type 변경 시 full scale 갱신
        self.box_states[index]["gas_type_var"].trace_add(
            "write",
            lambda *args, var=self.box_states[index]["gas_type_var"], idx=index: self.update_full_scale(var, idx)
        )

        # control_frame: IP 입력창 & 연결/해제 버튼이 들어갈 영역
        control_frame = Frame(inner_frame, bg="black")
        # grid(row=1, column=0)로 아래쪽 배치
        control_frame.grid(row=1, column=0, padx=0, pady=0, sticky="n")

        # IP 입력창 추가
        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        # AL1, AL2, PWR, FUT 표시용 원
        circle_items = []

        # AL1
        circle_items.append(box_canvas.create_oval(
            int(133 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(123 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        ))
        box_canvas.create_text(
            int(95 * SCALE_FACTOR) - int(25 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="AL1",
            fill="#cccccc",
            anchor="e"
        )

        # AL2
        circle_items.append(box_canvas.create_oval(
            int(77 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(87 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        ))
        box_canvas.create_text(
            int(140 * SCALE_FACTOR) - int(35 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="AL2",
            fill="#cccccc",
            anchor="e"
        )

        # PWR
        circle_items.append(box_canvas.create_oval(
            int(30 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(40 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        ))
        box_canvas.create_text(
            int(35 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="PWR",
            fill="#cccccc",
            anchor="center"
        )

        # FUT
        circle_items.append(box_canvas.create_oval(
            int(171 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(181 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        ))
        box_canvas.create_text(
            int(175 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            int(217 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="FUT",
            fill="#cccccc",
            anchor="n"
        )

        # 현재 박스에 해당하는 GAS Type 표시
        gas_type_var = self.box_states[index]["gas_type_var"]
        gas_type_text_id = box_canvas.create_text(
            *self.GAS_TYPE_POSITIONS[gas_type_var.get()],
            text=gas_type_var.get(),
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )
        self.box_states[index]["gas_type_text_id"] = gas_type_text_id

        # 모델명, 회사명
        box_canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(270 * SCALE_FACTOR),
            text="GMS-1000",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )
        box_canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(295 * SCALE_FACTOR),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica", int(7 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )

        # 바 그래프
        bar_canvas = Canvas(box_canvas,
                            width=int(120 * SCALE_FACTOR),
                            height=int(5 * SCALE_FACTOR),
                            bg="white",
                            highlightthickness=0)
        # bar_canvas도 grid가 아닌, Canvas 위에 배치할 때는 coords로 표현, 
        # 여기서는 직접 place보다 coords 이동으로도 가능하지만, 코드 단순화를 위해 create_window 사용.
        box_canvas.create_window(
            int(18.5 * SCALE_FACTOR),
            int(75 * SCALE_FACTOR),
            window=bar_canvas,
            anchor="nw"
        )
        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_data.append((box_canvas, circle_items, bar_canvas, bar_image, bar_item))

        # 처음엔 바 그래프, 알람 OFF
        self.show_bar(index, show=False)
        self.update_circle_state([False, False, False, False], box_index=index)

    def add_ip_row(self, parent_frame, ip_var, index):
        """
        IP 입력 Entry와 연결/해제 버튼을 나란히 grid로 배치
        """
        # IP 입력 Entry
        entry = Entry(
            parent_frame,
            textvariable=ip_var,
            width=int(11 * SCALE_FACTOR),
            highlightthickness=0,
            bd=0,
            relief='flat'
        )
        placeholder_text = f"{index + 1}. IP를 입력해주세요."
        if ip_var.get() == '':
            entry.insert(0, placeholder_text)
            entry.config(fg="grey")
        else:
            entry.config(fg="black")

        entry.bind("<FocusIn>",  lambda e, ent=entry, p=placeholder_text: self.on_focus_in(e, ent, p))
        entry.bind("<FocusOut>", lambda e, ent=entry, p=placeholder_text: self.on_focus_out(e, ent, p))
        entry.bind("<Button-1>", lambda e, ent=entry, p=placeholder_text: self.on_entry_click(e, ent, p))

        entry.grid(row=0, column=0, padx=(0, 10), pady=5)

        # 연결/해제 버튼
        action_button = Button(
            parent_frame,
            image=self.connect_image,
            command=lambda i=index: self.toggle_connection(i),
            width=int(60 * SCALE_FACTOR),
            height=int(40 * SCALE_FACTOR),
            bd=0,
            highlightthickness=0,
            borderwidth=0,
            relief='flat',
            bg='black',
            activebackground='black'
        )
        action_button.grid(row=0, column=1, padx=(0, 0), pady=5)

        self.entries.append(entry)
        self.action_buttons.append(action_button)

    def on_focus_in(self, event, entry, placeholder):
        if entry['state'] == 'normal':
            if entry.get() == placeholder:
                entry.delete(0, "end")
                entry.config(fg="black")
            entry.config(
                highlightthickness=1,
                highlightbackground="blue",
                highlightcolor="blue",
                bd=1,
                relief='solid'
            )

    def on_focus_out(self, event, entry, placeholder):
        if entry['state'] == 'normal':
            if not entry.get():
                entry.insert(0, placeholder)
                entry.config(fg="grey")
            entry.config(highlightthickness=0, bd=0, relief='flat')

    def on_entry_click(self, event, entry, placeholder):
        if entry['state'] == 'normal':
            self.on_focus_in(event, entry, placeholder)
            self.show_virtual_keyboard(entry)

    def show_virtual_keyboard(self, entry):
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_data[box_index][0]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]["gas_type_text_id"], *position)
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

    def update_circle_state(self, states, box_index=0):
        """
        states = [AL1, AL2, PWR, FUT] 순서의 bool 리스트
        """
        box_canvas, circle_items, *_ = self.box_data[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        # 알람 발생 여부 콜백
        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"modbus_{box_index}")

    def update_segment_display(self, value, box_index=0, blink=False):
        """
        7 세그먼트 표시 업데이트
        """
        box_canvas = self.box_data[box_index][0]
        value = value.zfill(4)
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.box_states[box_index]["previous_segment_display"] = value

        leading_zero = True
        for idx, digit in enumerate(value):
            if leading_zero and digit == '0' and idx < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS.get(digit, SEGMENTS[' '])
                leading_zero = False

            # 깜빡여야 하면 -> 모두 OFF
            if blink and self.box_states[box_index]["blink_state"]:
                segments = SEGMENTS[' ']

            for j, seg_state in enumerate(segments):
                color = '#fc0c0c' if seg_state == '1' else '#424242'
                segment_tag = f'segment_{idx}_{chr(97 + j)}'
                if box_canvas.segment_canvas.find_withtag(segment_tag):
                    box_canvas.segment_canvas.itemconfig(segment_tag, fill=color)

        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i)
        else:
            threading.Thread(target=self.connect, args=(i,)).start()

    def connect(self, i):
        ip = self.ip_vars[i].get()
        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502, timeout=3)
            if self.connect_to_server(ip, client):
                # 연결 성공
                stop_flag = threading.Event()
                self.stop_flags[ip] = stop_flag
                self.clients[ip] = client

                data_thread = threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, i),
                    daemon=True
                )
                self.connected_clients[ip] = data_thread
                data_thread.start()

                self.console.print(f"Started data thread for {ip}")
                self.parent.after(0, lambda: self.action_buttons[i].config(
                    image=self.disconnect_image,
                    relief='flat', borderwidth=0
                ))
                self.parent.after(0, lambda: self.entries[i].config(
                    state="disabled", highlightthickness=0, bd=0, relief='flat'
                ))
                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
                self.blink_pwr(i)
                self.save_ip_settings()

                # 테두리 제거
                self.parent.after(0, lambda: self.box_frames[i].config(highlightthickness=0))
            else:
                # 연결 실패
                self.console.print(f"Failed to connect to {ip}")
                self.parent.after(0, lambda: self.update_circle_state([False, False, False, False], box_index=i))

    def disconnect(self, i):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            threading.Thread(target=self.disconnect_client, args=(ip, i), daemon=True).start()

    def disconnect_client(self, ip, i):
        self.stop_flags[ip].set()
        self.connected_clients[ip].join(timeout=5)
        if self.connected_clients[ip].is_alive():
            self.console.print(f"Thread for {ip} did not terminate in time.")
        self.clients[ip].close()
        self.console.print(f"Disconnected from {ip}")
        self.cleanup_client(ip)
        self.parent.after(0, lambda: self.reset_ui_elements(i))
        self.parent.after(0, lambda: self.action_buttons[i].config(
            image=self.connect_image, relief='flat', borderwidth=0
        ))
        self.parent.after(0, lambda: self.entries[i].config(
            state="normal", highlightthickness=1, bd=0, relief='flat'
        ))
        self.parent.after(0, lambda: self.box_frames[i].config(highlightthickness=1))
        self.save_ip_settings()

    def reset_ui_elements(self, box_index):
        self.update_circle_state([False, False, False, False], box_index=box_index)
        self.update_segment_display("    ", box_index=box_index)
        self.show_bar(box_index, show=False)
        self.console.print(f"Reset UI elements for box {box_index}")

    def cleanup_client(self, ip):
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        """
        (예시) 단일 레지스터만 읽는 간단 버전.
        실제로는 multi read 등 수정 가능.
        """
        while not stop_flag.is_set():
            try:
                if client is None or not client.is_socket_open():
                    raise ConnectionException("Socket is closed")

                # 실제 주소에 맞게 수정
                address_40005 = 40005 - 1
                count = 1
                result_40005 = client.read_holding_registers(address_40005, count)
                if result_40005.isError():
                    raise ModbusIOException(f"Error reading from {ip} at address 40005")

                value_40005 = result_40005.registers[0]

                # 예: 에러 비트/알람 비트도 읽어서 ui_update_queue에 넣는 로직
                # 생략

                # 세그먼트 표시 업데이트
                self.data_queue.put((box_index, str(value_40005), False))

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

    def update_bar(self, value, box_index):
        _, _, bar_canvas, _, bar_item = self.box_data[box_index]
        percentage = value / 100.0
        bar_length = int(153 * SCALE_FACTOR * percentage)

        cropped_image = self.gradient_bar.crop((0, 0, bar_length, int(5 * SCALE_FACTOR)))
        bar_image = ImageTk.PhotoImage(cropped_image)
        bar_canvas.itemconfig(bar_item, image=bar_image)
        bar_canvas.bar_image = bar_image

    def show_bar(self, box_index, show):
        bar_canvas = self.box_data[box_index][2]
        bar_item = self.box_data[box_index][4]
        if show:
            bar_canvas.itemconfig(bar_item, state='normal')
        else:
            bar_canvas.itemconfig(bar_item, state='hidden')

    def connect_to_server(self, ip, client):
        retries = 5
        for attempt in range(retries):
            if client.connect():
                self.console.print(f"Connected to the Modbus server at {ip}")
                return True
            else:
                self.console.print(f"Connection attempt {attempt + 1} to {ip} failed. Retrying in 5 seconds...")
                time.sleep(5)
        return False

    def start_data_processing_thread(self):
        threading.Thread(target=self.process_data, daemon=True).start()

    def process_data(self):
        """
        세그먼트 표시 업데이트 등을 위한 큐 처리 스레드
        """
        while True:
            try:
                box_index, value, blink = self.data_queue.get(timeout=1)
                self.ui_update_queue.put(('segment_display', box_index, value, blink))
            except queue.Empty:
                continue

    def schedule_ui_update(self):
        """
        일정 주기로 UI 업데이트(큐 비우기)
        """
        self.parent.after(200, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        try:
            while not self.ui_update_queue.empty():
                item = self.ui_update_queue.get_nowait()
                if item[0] == 'segment_display':
                    _, box_index, value, blink = item
                    self.update_segment_display(value, box_index=box_index, blink=blink)
                elif item[0] == 'circle_state':
                    _, box_index, states = item
                    self.update_circle_state(states, box_index=box_index)
                elif item[0] == 'bar':
                    _, box_index, value = item
                    self.update_bar(value, box_index)

                # 필요하다면 alarm_check 등 다른 처리 가능

        except queue.Empty:
            pass
        finally:
            self.schedule_ui_update()

    def check_click(self, event):
        pass

    def handle_disconnection(self, box_index):
        self.ui_update_queue.put(('segment_display', box_index, "    ", False))
        self.ui_update_queue.put(('circle_state', box_index, [False, False, False, False]))
        self.ui_update_queue.put(('bar', box_index, 0))
        self.parent.after(0, lambda: self.action_buttons[box_index].config(
            image=self.connect_image, relief='flat', borderwidth=0
        ))
        self.parent.after(0, lambda: self.entries[box_index].config(
            state="normal", highlightthickness=1, bd=0, relief='flat'
        ))
        self.parent.after(0, lambda: self.box_frames[box_index].config(highlightthickness=1))
        self.parent.after(0, lambda: self.reset_ui_elements(box_index))

    def reconnect(self, ip, client, stop_flag, box_index):
        retries = 0
        max_retries = 5
        while not stop_flag.is_set() and retries < max_retries:
            time.sleep(5)
            self.console.print(f"Attempting to reconnect to {ip} (Attempt {retries + 1}/{max_retries})")
            if client.connect():
                self.console.print(f"Reconnected to the Modbus server at {ip}")
                stop_flag.clear()
                threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, box_index),
                    daemon=True
                ).start()

                # UI 변경
                self.parent.after(0, lambda: self.action_buttons[box_index].config(
                    image=self.disconnect_image, relief='flat', borderwidth=0
                ))
                self.parent.after(0, lambda: self.entries[box_index].config(
                    state="disabled", highlightthickness=0, bd=0, relief='flat'
                ))
                self.parent.after(0, lambda: self.box_frames[box_index].config(highlightthickness=0))
                self.ui_update_queue.put(('circle_state', box_index, [False, False, True, False]))
                self.blink_pwr(box_index)
                self.show_bar(box_index, show=True)
                break
            else:
                retries += 1
                self.console.print(f"Reconnect attempt to {ip} failed.")

        if retries >= max_retries:
            self.console.print(f"Failed to reconnect to {ip} after {max_retries} attempts.")
            self.disconnect_client(ip, box_index)

    def save_ip_settings(self):
        ip_settings = [ip_var.get() for ip_var in self.ip_vars]
        with open(self.SETTINGS_FILE, 'w') as file:
            json.dump(ip_settings, file)

    def blink_pwr(self, box_index):
        """
        PWR 램프 깜빡임
        """
        def toggle_color():
            box_canvas, circle_items, *_ = self.box_data[box_index]
            if self.box_states[box_index]["pwr_blink_state"]:
                box_canvas.itemconfig(circle_items[2], fill="red", outline="red")
            else:
                box_canvas.itemconfig(circle_items[2], fill="green", outline="green")

            self.box_states[box_index]["pwr_blink_state"] = not self.box_states[box_index]["pwr_blink_state"]

            # 연결 중이면 계속 깜빡
            if self.ip_vars[box_index].get() in self.connected_clients:
                self.parent.after(self.blink_interval, toggle_color)

        toggle_color()
