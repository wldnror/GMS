import json
import os
import time
from tkinter import Frame, Canvas, StringVar, Entry, Button, Toplevel, Tk
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
        "ORG": (int(115 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "ARF-T": (int(107 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "HMDS": (int(110 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "HC-100": (int(104 * SCALE_FACTOR), int(100 * SCALE_FACTOR))
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

        for i in range(num_boxes):
            self.create_modbus_box(i)

        # -----------------------------------------------------
        # 통신 주기는 고정 200ms
        # 알람 깜빡임은 1초로 따로 설정
        # -----------------------------------------------------
        self.communication_interval = 0.2  # 200ms (데이터 읽기 주기)
        self.blink_interval = int(self.communication_interval * 1000)  # PWR 램프 깜빡임은 기존대로 200ms
        self.alarm_blink_interval = 1000  # AL1/AL2 깜빡임은 1초 간격

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

    def add_ip_row(self, frame, ip_var, index):
        # Entry 테두리를 위한 프레임
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
        if ip_var.get() == '':
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
        self.entries.append(entry)

        action_button = Button(
            frame,
            image=self.connect_image,
            command=lambda i=index: self.toggle_connection(i),
            width=int(60 * SCALE_FACTOR),
            height=int(40 * SCALE_FACTOR),
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

    def show_virtual_keyboard(self, entry):
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    def create_modbus_box(self, index):
        box_frame = Frame(self.parent, highlightthickness=int(3 * SCALE_FACTOR))
        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(
            inner_frame,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000000",
            highlightcolor="#000000",
            bg="#1e1e1e"
        )
        box_canvas.pack()

        # 상단(회색), 하단(검정) 영역
        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR),
                                    fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, int(200 * SCALE_FACTOR), int(260 * SCALE_FACTOR), int(310 * SCALE_FACTOR),
                                    fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)

        # 초기 상태 세팅
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,
            "pwr_blink_state": False,
            "pwr_blinking": False,
            "gas_type_var": StringVar(value=self.gas_types.get(f"modbus_box_{index}", "ORG")),
            "gas_type_text_id": None,
            "full_scale": self.GAS_FULL_SCALE[self.gas_types.get(f"modbus_box_{index}", "ORG")],
            # 알람 상태
            "alarm1_on": False,
            "alarm2_on": False,
            "alarm1_blinking": False,
            "alarm2_blinking": False,
            "alarm_border_blink": False,  # 테두리 깜빡임 여부
            "border_blink_state": False   # 테두리 현재 깜빡임 상태(토글용)
        })

        self.box_states[index]["gas_type_var"].trace_add(
            "write",
            lambda *args, var=self.box_states[index]["gas_type_var"], idx=index: self.update_full_scale(var, idx)
        )

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=int(10 * SCALE_FACTOR), y=int(205 * SCALE_FACTOR))

        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        # -------------------------------------------------------
        #  AL1(0), AL2(1), PWR(2), FUT(3) 순서로 circle_items에 저장
        # -------------------------------------------------------

        # AL1
        circle_items = []
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
            text="AL1",
            fill="#cccccc",
            anchor="e"
        )

        # AL2
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
            text="AL2",
            fill="#cccccc",
            anchor="e"
        )

        # PWR
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

        # FUT
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

        gas_type_var = self.box_states[index]["gas_type_var"]
        gas_type_text_id = box_canvas.create_text(
            *self.GAS_TYPE_POSITIONS[gas_type_var.get()],
            text=gas_type_var.get(),
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )
        self.box_states[index]["gas_type_text_id"] = gas_type_text_id

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

        bar_canvas = Canvas(
            box_canvas,
            width=int(120 * SCALE_FACTOR),
            height=int(5 * SCALE_FACTOR),
            bg="white",
            highlightthickness=0
        )
        bar_canvas.place(x=int(18.5 * SCALE_FACTOR), y=int(75 * SCALE_FACTOR))

        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append(box_frame)
        self.box_data.append((box_canvas, circle_items, bar_canvas, bar_image, bar_item))

        self.show_bar(index, show=False)
        self.update_circle_state([False, False, False, False], box_index=index)

        box_frame.pack(side="left", padx=10, pady=10)

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
        box_canvas, circle_items, _, _, _ = self.box_data[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        # 알람 발생 여부 콜백
        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"modbus_{box_index}")

    def update_segment_display(self, value, box_index=0, blink=False):
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
                segments = SEGMENTS[digit]
                leading_zero = False

            # 깜빡임 상태이면 세그먼트를 꺼버림
            if blink and self.box_states[box_index]["blink_state"]:
                segments = SEGMENTS[' ']

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                segment_tag = f'segment_{idx}_{chr(97 + j)}'
                if box_canvas.segment_canvas.find_withtag(segment_tag):
                    box_canvas.segment_canvas.itemconfig(segment_tag, fill=color)

        # 다음 호출 시 깜빡임 토글
        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i)
        else:
            threading.Thread(target=self.connect, args=(i,), daemon=True).start()

    def connect(self, i):
        ip = self.ip_vars[i].get()
        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502, timeout=3)
            if self.connect_to_server(ip, client):
                # 연결 성공
                stop_flag = threading.Event()
                self.stop_flags[ip] = stop_flag
                self.clients[ip] = client
                self.connected_clients[ip] = threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, i)
                )
                self.connected_clients[ip].daemon = True
                self.connected_clients[ip].start()
                self.console.print(f"Started data thread for {ip}")

                self.parent.after(0, lambda: self.action_buttons[i].config(image=self.disconnect_image, relief='flat', borderwidth=0))
                self.parent.after(0, lambda: self.entries[i].config(state="disabled"))
                self.update_circle_state([False, False, True, False], box_index=i)  # PWR 켜짐
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
        self.parent.after(0, lambda: self.action_buttons[i].config(image=self.connect_image, relief='flat', borderwidth=0))
        self.parent.after(0, lambda: self.entries[i].config(state="normal"))
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
        while not stop_flag.is_set():
            try:
                if client is None or not client.is_socket_open():
                    raise ConnectionException("Socket is closed")

                address_40001 = 40001 - 1
                address_40005 = 40005 - 1
                address_40007 = 40008 - 1
                address_40011 = 40011 - 1
                count = 1

                result_40001 = client.read_holding_registers(address_40001, count)
                result_40005 = client.read_holding_registers(address_40005, count)
                result_40007 = client.read_holding_registers(address_40007, count)
                result_40011 = client.read_holding_registers(address_40011, count)

                if result_40001.isError():
                    raise ModbusIOException(f"Error reading from {ip} at address 40001")

                # Alarm bits 확인
                value_40001 = result_40001.registers[0]
                bit_6_on = bool(value_40001 & (1 << 6))  # ALARM1
                bit_7_on = bool(value_40001 & (1 << 7))  # ALARM2

                # 읽은 알람 상태를 box_states에 저장
                self.box_states[box_index]["alarm1_on"] = bit_6_on
                self.box_states[box_index]["alarm2_on"] = bit_7_on
                # UI 쪽에서 알람 깜빡임 로직 처리
                self.ui_update_queue.put(('alarm_check', box_index))

                if result_40005.isError():
                    raise ModbusIOException(f"Error reading from {ip} at address 40005")

                value_40005 = result_40005.registers[0]
                self.box_states[box_index]["last_value_40005"] = value_40005

                if result_40007.isError():
                    raise ModbusIOException(f"Error reading from {ip} at address 40007")

                value_40007 = result_40007.registers[0]
                bits = [bool(value_40007 & (1 << n)) for n in range(4)]

                if not any(bits):
                    formatted_value = f"{value_40005}"
                    self.data_queue.put((box_index, formatted_value, False))
                else:
                    error_display = ""
                    for i, bit in enumerate(bits):
                        if bit:
                            error_display = BIT_TO_SEGMENT[i]
                            break

                    error_display = error_display.ljust(4)
                    if 'E' in error_display:
                        self.box_states[box_index]["blinking_error"] = True
                        self.data_queue.put((box_index, error_display, True))
                        self.ui_update_queue.put(('circle_state', box_index, [False, False, True, self.box_states[box_index]["blink_state"]]))
                    else:
                        self.box_states[box_index]["blinking_error"] = False
                        self.data_queue.put((box_index, error_display, False))
                        self.ui_update_queue.put(('circle_state', box_index, [False, False, True, False]))

                if result_40011.isError():
                    raise ModbusIOException(f"Error reading from {ip} at address 40011")

                value_40011 = result_40011.registers[0]
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
        while True:
            try:
                box_index, value, blink = self.data_queue.get(timeout=1)
                self.ui_update_queue.put(('segment_display', box_index, value, blink))
            except queue.Empty:
                continue

    def schedule_ui_update(self):
        self.parent.after(200, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        try:
            while not self.ui_update_queue.empty():
                item = self.ui_update_queue.get_nowait()
                if item[0] == 'circle_state':
                    _, box_index, states = item
                    self.update_circle_state(states, box_index=box_index)
                elif item[0] == 'bar':
                    _, box_index, value = item
                    self.update_bar(value, box_index)
                elif item[0] == 'segment_display':
                    _, box_index, value, blink = item
                    self.update_segment_display(value, box_index=box_index, blink=blink)
                elif item[0] == 'alarm_check':
                    # Alarm 비트 변경에 따른 AL1, AL2 램프 깜빡임 로직
                    box_index = item[1]
                    self.check_alarms(box_index)
        except queue.Empty:
            pass
        finally:
            self.schedule_ui_update()

    def check_click(self, event):
        pass

    def handle_disconnection(self, box_index):
        self.ui_update_queue.put(('circle_state', box_index, [False, False, False, False]))
        self.ui_update_queue.put(('segment_display', box_index, "    ", False))
        self.ui_update_queue.put(('bar', box_index, 0))
        self.parent.after(0, lambda: self.action_buttons[box_index].config(image=self.connect_image, relief='flat', borderwidth=0))
        self.parent.after(0, lambda: self.entries[box_index].config(state="normal"))
        self.parent.after(0, lambda: self.box_frames[box_index].config(highlightthickness=1))
        self.parent.after(0, lambda: self.reset_ui_elements(box_index))

        # PWR 램프 깜빡임도 정지
        self.box_states[box_index]["pwr_blink_state"] = False
        self.box_states[box_index]["pwr_blinking"] = False

        box_canvas = self.box_data[box_index][0]
        circle_items = self.box_data[box_index][1]
        box_canvas.itemconfig(circle_items[2], fill="#e0fbba", outline="#e0fbba")
        self.console.print(f"PWR lamp set to default green for box {box_index} due to disconnection.")

    def reconnect(self, ip, client, stop_flag, box_index):
        retries = 0
        max_retries = 5
        while not stop_flag.is_set() and retries < max_retries:
            time.sleep(5)
            self.console.print(f"Attempting to reconnect to {ip} (Attempt {retries + 1}/{max_retries})")
            if client.connect():
                self.console.print(f"Reconnected to the Modbus server at {ip}")
                stop_flag.clear()
                threading.Thread(target=self.read_modbus_data, args=(ip, client, stop_flag, box_index), daemon=True).start()
                self.parent.after(0, lambda: self.action_buttons[box_index].config(image=self.disconnect_image, relief='flat', borderwidth=0))
                self.parent.after(0, lambda: self.entries[box_index].config(state="disabled"))
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
        # 이미 깜빡이고 있는지 확인
        if self.box_states[box_index].get("pwr_blinking", False):
            return

        # 깜빡임 시작
        self.box_states[box_index]["pwr_blinking"] = True

        def toggle_color():
            if not self.box_states[box_index]["pwr_blinking"]:
                # 깜빡임 중지
                return

            # 연결 상태를 확인
            if self.ip_vars[box_index].get() not in self.connected_clients:
                box_canvas = self.box_data[box_index][0]
                circle_items = self.box_data[box_index][1]
                box_canvas.itemconfig(circle_items[2], fill="#e0fbba", outline="#e0fbba")
                self.box_states[box_index]["pwr_blink_state"] = False
                self.box_states[box_index]["pwr_blinking"] = False
                return

            box_canvas = self.box_data[box_index][0]
            circle_items = self.box_data[box_index][1]
            if self.box_states[box_index]["pwr_blink_state"]:
                box_canvas.itemconfig(circle_items[2], fill="red", outline="red")
            else:
                box_canvas.itemconfig(circle_items[2], fill="green", outline="green")
            self.box_states[box_index]["pwr_blink_state"] = not self.box_states[box_index]["pwr_blink_state"]
            if self.ip_vars[box_index].get() in self.connected_clients:
                self.parent.after(self.blink_interval, toggle_color)  # PWR는 200ms로 유지

        toggle_color()

    # -------------------------------------------------------------------------
    #  추가된 알람 체크/깜빡임 함수 (AL1/AL2 램프 깜빡임은 alarm_blink_interval=1000ms)
    # -------------------------------------------------------------------------
    def check_alarms(self, box_index):
        """
        - Alarm2(bit7)가 울리면 Alarm1 램프는 빨간색 고정 켜짐, Alarm2 깜빡임.
        - Alarm1(bit6)만 울리면 AL1 깜빡임, AL2는 off.
        - 둘 다 없으면 깜빡임 해제.
        """
        alarm1 = self.box_states[box_index]["alarm1_on"]
        alarm2 = self.box_states[box_index]["alarm2_on"]

        if alarm2:
            # Alarm2가 켜져 있으면, Alarm1은 "깜빡임 없이" 빨간색 유지
            self.box_states[box_index]["alarm1_blinking"] = False
            self.box_states[box_index]["alarm2_blinking"] = True
            self.set_alarm_lamp(box_index, alarm1_on=True, blink1=False, alarm2_on=True, blink2=True)
            # 테두리도 깜빡이도록
            self.box_states[box_index]["alarm_border_blink"] = True
            self.blink_alarms(box_index)

        elif alarm1:
            # Alarm1만 켜져 있으면 AL1 깜빡임, AL2는 꺼짐
            self.box_states[box_index]["alarm1_blinking"] = True
            self.box_states[box_index]["alarm2_blinking"] = False
            self.set_alarm_lamp(box_index, alarm1_on=True, blink1=True, alarm2_on=False, blink2=False)
            # 테두리 깜빡임
            self.box_states[box_index]["alarm_border_blink"] = True
            self.blink_alarms(box_index)

        else:
            # 둘 다 꺼졌으면 깜빡임 해제
            self.box_states[box_index]["alarm1_blinking"] = False
            self.box_states[box_index]["alarm2_blinking"] = False
            self.box_states[box_index]["alarm_border_blink"] = False

            self.set_alarm_lamp(box_index, alarm1_on=False, blink1=False, alarm2_on=False, blink2=False)
            # 테두리 색상 기본(검정)
            box_canvas = self.box_data[box_index][0]
            box_canvas.config(highlightbackground="#000000")
            self.box_states[box_index]["border_blink_state"] = False

    def set_alarm_lamp(self, box_index, alarm1_on, blink1, alarm2_on, blink2):
        """
        실제로 AL1, AL2 램프 색을 세팅.
        blink1, blink2는 ‘지금 이 램프가 깜빡임 대상인지’를 나타냅니다.
        """
        box_canvas, circle_items, *_ = self.box_data[box_index]

        # AL1
        if alarm1_on:
            if blink1:
                # 깜빡임 대상이면 우선 off 상태로 시작
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
        """
        Alarm1/Alarm2 램프 + 테두리 깜빡임을 (alarm_blink_interval=1000ms)마다 토글
        """
        if not (self.box_states[box_index]["alarm1_blinking"] or
                self.box_states[box_index]["alarm2_blinking"] or
                self.box_states[box_index]["alarm_border_blink"]):
            return

        box_canvas, circle_items, *_ = self.box_data[box_index]
        state = self.box_states[box_index]["border_blink_state"]
        self.box_states[box_index]["border_blink_state"] = not state

        # 테두리 색상 토글 (빨강 ↔ 검정)
        if self.box_states[box_index]["alarm_border_blink"]:
            if state:
                box_canvas.config(highlightbackground="#000000")
            else:
                box_canvas.config(highlightbackground="#ff0000")

        # AL1 깜빡임
        if self.box_states[box_index]["alarm1_blinking"]:
            fill_now = box_canvas.itemcget(circle_items[0], "fill")
            if fill_now == "red":
                box_canvas.itemconfig(circle_items[0], fill="#fdc8c8", outline="#fdc8c8")
            else:
                box_canvas.itemconfig(circle_items[0], fill="red", outline="red")

        # AL2 깜빡임
        if self.box_states[box_index]["alarm2_blinking"]:
            fill_now = box_canvas.itemcget(circle_items[1], "fill")
            if fill_now == "red":
                box_canvas.itemconfig(circle_items[1], fill="#fdc8c8", outline="#fdc8c8")
            else:
                box_canvas.itemconfig(circle_items[1], fill="red", outline="red")

        # 여기서 AL1/AL2의 깜빡임은 self.alarm_blink_interval 적용
        self.parent.after(self.alarm_blink_interval, lambda: self.blink_alarms(box_index))

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
    root.mainloop()

if __name__ == "__main__":
    main()
