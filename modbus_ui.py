import json
import os
import time
from tkinter import Frame, Canvas, StringVar, Entry, Button, Toplevel, Label, messagebox
import threading
import queue
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException
from rich.console import Console
from PIL import Image, ImageTk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplcursors
from common import SEGMENTS, BIT_TO_SEGMENT, create_gradient_bar, create_segment_display
from virtual_keyboard import VirtualKeyboard

class ModbusUI:
    LOGS_PER_FILE = 10  # 로그 파일당 저장할 로그 개수
    SETTINGS_FILE = "modbus_settings.json"  # IP 설정 파일
    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }
    
    GAS_TYPE_POSITIONS = {
        "ORG": (149, 122),
        "ARF-T": (140, 122),
        "HMDS": (143, 122),
        "HC-100": (139, 122)
    }


    def __init__(self, root, num_boxes, gas_types, alarm_callback):
        self.root = root
        self.alarm_callback = alarm_callback  # 알람 콜백 추가
        self.virtual_keyboard = VirtualKeyboard(root)
        self.ip_vars = [StringVar() for _ in range(num_boxes)]  # IP 변수 초기화
        self.entries = []
        self.action_buttons = []
        self.clients = {}
        self.connected_clients = {}
        self.stop_flags = {}
        self.data_queue = queue.Queue()
        self.console = Console()
        self.box_states = []
        self.graph_windows = [None for _ in range(num_boxes)]
        self.history_window = None  # 히스토리 창을 저장할 변수
        self.history_lock = threading.Lock()  # 히스토리 창 중복 방지를 위한 락
        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=20, pady=20)
        self.row_frames = []
        self.box_frames = []
        self.gradient_bar = create_gradient_bar(153, 5)
        self.history_dir = "history_logs"
        self.gas_types = gas_types

        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        self.load_ip_settings(num_boxes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, "img/on.png")
        disconnect_image_path = os.path.join(script_dir, "img/off.png")

        self.connect_image = self.load_image(connect_image_path, (50, 70))
        self.disconnect_image = self.load_image(disconnect_image_path, (50, 70))

        for i in range(num_boxes):
            self.create_modbus_box(i)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        self.root.after(100, self.process_queue)
        self.root.bind("<Button-1>", self.check_click)

    def load_image(self, path, size):
        img = Image.open(path).convert("RGBA")
        img.thumbnail(size, Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def add_ip_row(self, frame, ip_var, index):
        entry = Entry(frame, textvariable=ip_var, width=16, highlightthickness=0)
        placeholder_text = f"{index + 1}. IP를 입력해주세요."
        if ip_var.get() == '':
            entry.insert(0, placeholder_text)
            entry.config(fg="grey")

        entry.bind("<FocusIn>", lambda event, e=entry, p=placeholder_text: self.on_focus_in(event, e, p))
        entry.bind("<FocusOut>", lambda event, e=entry, p=placeholder_text: self.on_focus_out(event, e, p))
        entry.bind("<Button-1>", lambda event, e=entry, p=placeholder_text: self.on_entry_click(event, e, p))
        entry.grid(row=0, column=0, padx=(0, 5))
        self.entries.append(entry)

        action_button = Button(frame, image=self.connect_image, command=lambda i=index: self.toggle_connection(i),
                               width=60, height=40, bd=0, highlightthickness=0, borderwidth=0, relief='flat', bg='black', activebackground='black')
        action_button.grid(row=0, column=1, padx=(0, 0))
        self.action_buttons.append(action_button)

    def show_virtual_keyboard(self, entry):
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    def on_focus_in(self, event, entry, placeholder):
        if entry.get() == placeholder:
            entry.delete(0, "end")
            entry.config(fg="black")

    def on_focus_out(self, event, entry, placeholder):
        if not entry.get():
            entry.insert(0, placeholder)
            entry.config(fg="grey")

    def on_entry_click(self, event, entry, placeholder):
        self.on_focus_in(event, entry, placeholder)
        self.show_virtual_keyboard(entry)

    def create_modbus_box(self, index):
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

        create_segment_display(box_canvas)
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None,
            "pwr_blink_state": False,  # PWR 깜빡임 상태 초기화
            "gas_type_var": StringVar(value=self.gas_types.get(f"modbus_box_{index}", "ORG")),
            "gas_type_text_id": None,
            "full_scale": self.GAS_FULL_SCALE[self.gas_types.get(f"modbus_box_{index}", "ORG")]
        })

        self.box_states[index]["gas_type_var"].trace_add("write", lambda *args, var=self.box_states[index]["gas_type_var"], idx=index: self.update_full_scale(var, idx))

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=10, y=250)

        ip_var = self.ip_vars[index]

        self.add_ip_row(control_frame, ip_var, index)

        circle_items = []

        circle_items.append(box_canvas.create_oval(133, 200, 123, 190))
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(77, 200, 87, 190))
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(30, 200, 40, 190))
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(171, 200, 181, 190))
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        gas_type_var = self.box_states[index]["gas_type_var"]
        gas_type_text_id = box_canvas.create_text(*self.GAS_TYPE_POSITIONS[gas_type_var.get()], text=gas_type_var.get(), font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")
        self.box_states[index]["gas_type_text_id"] = gas_type_text_id

        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc", anchor="center")

        bar_canvas = Canvas(box_canvas, width=153, height=5, bg="white", highlightthickness=0)
        bar_canvas.place(x=27, y=98)

        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append((box_frame, box_canvas, circle_items, bar_canvas, bar_image, bar_item))

        self.show_bar(index, show=False)

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
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]
        
        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'
        
        for i, state in enumerate(states):
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

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i)
        else:
            threading.Thread(target=self.connect, args=(i,)).start()

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
                self.root.after(0, lambda: self.action_buttons[i].config(image=self.disconnect_image, relief='flat', borderwidth=0))
                self.root.after(0, lambda: self.entries[i].config(state="disabled"))  # 필드값 입력 막기
                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()  # 연결 후 가상 키보드 숨기기
                self.blink_pwr(i)  # PWR 깜빡이기 시작
                self.save_ip_settings()  # 연결된 IP 저장
            else:
                self.console.print(f"Failed to connect to {ip}")

    def disconnect(self, i):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            self.stop_flags[ip].set()
            self.clients[ip].close()
            self.console.print(f"Disconnected from {ip}")
            self.connected_clients[ip].join()
            self.cleanup_client(ip)
            self.ip_vars[i].set('')
            self.action_buttons[i].config(image=self.connect_image, relief='flat', borderwidth=0)
            self.root.after(0, lambda: self.entries[i].config(state="normal"))  # 필드값 입력 가능하게 하기
            self.update_circle_state([False, False, False, False], box_index=i)
            self.update_segment_display("    ", self.box_frames[i][1], box_index=i)
            self.show_bar(i, show=False)
            self.save_ip_settings()  # 연결이 끊어진 경우에도 IP 저장

    def cleanup_client(self, ip):
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        blink_state_middle = False
        blink_state_top = False
        interval = 0.4
        next_call = time.time()
        while not stop_flag.is_set():
            try:
                if not client.is_socket_open():
                    raise ConnectionException("Socket is closed")

                address_40001 = 40001 - 1
                address_40005 = 40005 - 1
                address_40007 = 40008 - 1
                address_40011 = 40011 - 1
                count = 1
                result_40001 = client.read_holding_registers(address_40001, count, unit=1)
                result_40005 = client.read_holding_registers(address_40005, count, unit=1)
                result_40007 = client.read_holding_registers(address_40007, count, unit=1)
                result_40011 = client.read_holding_registers(address_40011, count, unit=1)

                if not result_40001.isError():
                    value_40001 = result_40001.registers[0]

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

                    self.update_circle_state([top_blink, middle_blink, middle_fixed, False], box_index=box_index)

                if not result_40005.isError():
                    value_40005 = result_40005.registers[0]
                    self.box_states[box_index]["last_value_40005"] = value_40005

                    if not result_40007.isError():
                        value_40007 = result_40007.registers[0]

                        bits = [bool(value_40007 & (1 << n)) for n in range(4)]

                        if not any(bits):
                            formatted_value = f"{value_40005:04d}"
                            self.data_queue.put((box_index, formatted_value, False))
                        else:
                            error_display = ""
                            for i, bit in enumerate(bits):
                                if bit:
                                    error_display = BIT_TO_SEGMENT[i]
                                    self.record_history(box_index, error_display)
                                    break

                            error_display = error_display.ljust(4)

                            if 'E' in error_display:
                                self.box_states[box_index]["blinking_error"] = True
                                self.data_queue.put((box_index, error_display, True))
                                self.update_circle_state([False, False, True, self.box_states[box_index]["blink_state"]],
                                                         box_index=box_index)
                            else:
                                self.box_states[box_index]["blinking_error"] = False
                                self.data_queue.put((box_index, error_display, False))
                                self.update_circle_state([False, False, True, False], box_index=box_index)
                    else:
                        self.console.print(f"Error from {ip}: {result_40007}")
                else:
                    self.console.print(f"Error from {ip}: {result_40005}")

                if not result_40011.isError():
                    value_40011 = result_40011.registers[0]
                    self.update_bar(value_40011, self.box_frames[box_index][3], self.box_frames[box_index][5])

                next_call += interval
                sleep_time = next_call - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_call = time.time()

            except ConnectionException:
                self.console.print(f"Connection to {ip} lost. Attempting to reconnect...")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break
            except AttributeError as e:
                self.console.print(f"Error reading data from {ip}: {e}")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break

    def update_bar(self, value, bar_canvas, bar_item):
        percentage = value / 100.0
        bar_length = int(153 * percentage)

        cropped_image = self.gradient_bar.crop((0, 0, bar_length, 5))
        bar_image = ImageTk.PhotoImage(cropped_image)
        bar_canvas.itemconfig(bar_item, image=bar_image)
        bar_canvas.bar_image = bar_image

    def show_bar(self, box_index, show):
        bar_canvas, _, bar_item = self.box_frames[box_index][3:6]
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

    def process_queue(self):
        while not self.data_queue.empty():
            box_index, value, blink = self.data_queue.get()
            box_canvas = self.box_frames[box_index][1]
            self.update_segment_display(value, box_canvas, blink=blink, box_index=box_index)
        self.root.after(100, self.process_queue)

    def check_click(self, event):
        if hasattr(self, 'history_frame') and self.history_frame.winfo_exists():
            widget = event.widget
            if widget != self.history_frame and not self.history_frame.winfo_containing(event.x_root, event.y_root):
                self.hide_history(event)

    def handle_disconnection(self, box_index):
        self.update_circle_state([False, False, False, False], box_index=box_index)
        self.update_segment_display("    ", self.box_frames[box_index][1], box_index=box_index)
        self.show_bar(box_index, show=False)
        self.root.after(0, lambda: self.action_buttons[box_index].config(image=self.connect_image, relief='flat', borderwidth=0))
        self.root.after(0, lambda: self.entries[box_index].config(state="normal"))  # 필드값 입력 가능하게 하기

    def reconnect(self, ip, client, stop_flag, box_index):
        while not stop_flag.is_set():
            if client.connect():
                self.console.print(f"Reconnected to the Modbus server at {ip}")
                stop_flag.clear()
                threading.Thread(target=self.read_modbus_data, args=(ip, client, stop_flag, box_index)).start()
                self.root.after(0, lambda: self.action_buttons[box_index].config(image=self.disconnect_image, relief='flat', borderwidth=0))
                self.root.after(0, lambda: self.entries[box_index].config(state="disabled"))  # 필드값 입력 막기
                self.update_circle_state([False, False, True, False], box_index=box_index)
                self.blink_pwr(box_index)  # PWR 깜빡이기 시작
                self.show_bar(box_index, show=True)
                break
            else:
                self.console.print(f"Reconnect attempt to {ip} failed. Retrying in 1 second...")
                time.sleep(1)

    def save_ip_settings(self):
        ip_settings = [ip_var.get() for ip_var in self.ip_vars]
        with open(self.SETTINGS_FILE, 'w') as file:
            json.dump(ip_settings, file)

    def load_ip_settings(self, num_boxes):
        if os.path.exists(self.SETTINGS_FILE):
            with open(self.SETTINGS_FILE, 'r') as file:
                ip_settings = json.load(file)
                for i in range(min(num_boxes, len(ip_settings))):
                    self.ip_vars[i].set(ip_settings[i])
        else:
            self.ip_vars = [StringVar() for _ in range(num_boxes)]

    def blink_pwr(self, box_index):
        def toggle_color():
            if self.box_states[box_index]["pwr_blink_state"]:
                self.box_frames[box_index][1].itemconfig(self.box_frames[box_index][2][2], fill="blue", outline="blue")
            else:
                self.box_frames[box_index][1].itemconfig(self.box_frames[box_index][2][2], fill="green", outline="green")
            self.box_states[box_index]["pwr_blink_state"] = not self.box_states[box_index]["pwr_blink_state"]
            if self.ip_vars[box_index].get() in self.connected_clients:
                self.root.after(600, toggle_color)

        toggle_color()

# Main Application Code
def show_box_settings():
    global box_settings_window
    if box_settings_window and box_settings_window.winfo_exists():
        box_settings_window.focus()
        return

    box_settings_window = Toplevel(root)
    box_settings_window.title("상자 설정")
    box_settings_window.attributes("-topmost", True)

    def create_gas_type_menu(parent, box_index):
        options = ["ORG", "ARF-T   ", "HMDS  ", "HC-100   "]
        var = StringVar(value=settings["gas_types"].get(f"box_{box_index}", "ORG"))
        menu = OptionMenu(parent, var, *options)
        menu.grid(row=box_index, column=2, padx=5, pady=5)
        var.trace_add("write", lambda *args, v=var, i=box_index: settings["gas_types"].update({f"box_{i}": v.get()}))
        return var

    gas_type_vars = []

    Label(box_settings_window, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
    modbus_entry = Entry(box_settings_window, font=("Arial", 12))
    modbus_entry.insert(0, settings["modbus_boxes"])
    modbus_entry.grid(row=0, column=1, padx=5, pady=5)
    create_keypad(modbus_entry)

    Label(box_settings_window, text="4~20mA 상자 수", font=("Arial", 12)).grid(row=1, column=0, padx=5, pady=5)
    analog_entry = Entry(box_settings_window, font=("Arial", 12))
    analog_entry.insert(0, settings["analog_boxes"])
    analog_entry.grid(row=1, column=1, padx=5, pady=5)
    create_keypad(analog_entry)

    for i in range(max(settings["modbus_boxes"], settings["analog_boxes"])):
        Label(box_settings_window, text=f"상자 {i+1}:", font=("Arial", 12)).grid(row=i + 2, column=0, padx=5)
        gas_type_var = create_gas_type_menu(box_settings_window, i)
        gas_type_vars.append(gas_type_var)

    def save_and_close():
        try:
            settings["modbus_boxes"] = int(modbus_entry.get())
            settings["analog_boxes"] = int(analog_entry.get())
            for i, var in enumerate(gas_type_vars):
                settings["gas_types"][f"box_{i}"] = var.get()
            save_settings(settings)
            messagebox.showinfo("설정 저장", "설정이 저장되었습니다.")
            box_settings_window.destroy()
            restart_application()  # 설정이 변경되면 애플리케이션을 재시작
        except ValueError:
            messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=max(settings["modbus_boxes"], settings["analog_boxes"]) + 2, columnspan=3, pady=10)

# Rest of the main.py code remains the same
