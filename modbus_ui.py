import os
import time
from tkinter import Frame, Canvas, StringVar, DISABLED, NORMAL, Entry, Button, Toplevel, Scrollbar, HORIZONTAL
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

    def __init__(self, root, num_boxes):
        self.root = root
        self.virtual_keyboard = VirtualKeyboard(root)
        self.ip_vars = []
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

        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, "img/on.png")
        disconnect_image_path = os.path.join(script_dir, "img/off.png")

        self.connect_image = self.load_image(connect_image_path, (50, 70))
        self.disconnect_image = self.load_image(disconnect_image_path, (50, 70))

        for _ in range(num_boxes):
            self.create_modbus_box()

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
        entry.insert(0, placeholder_text)
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

    def create_modbus_box(self):
        i = len(self.box_frames)
        row = i // 7
        col = i % 7

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
            "last_history_value": None
        })
        self.update_segment_display("    ", box_canvas, box_index=i)

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=10, y=250)

        ip_var = StringVar()
        self.ip_vars.append(ip_var)

        self.add_ip_row(control_frame, ip_var, i)

        circle_items = []

        circle_items.append(box_canvas.create_oval(133, 200, 123, 190))
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(77, 200, 87, 190))
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(30, 200, 40, 190))
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(171, 200, 181, 190))
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        box_canvas.create_text(129, 105, text="ORG", font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc", anchor="center")

        bar_canvas = Canvas(box_canvas, width=153, height=5, bg="white", highlightthickness=0)
        bar_canvas.place(x=27, y=98)

        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append((box_frame, box_canvas, circle_items, bar_canvas, bar_image, bar_item))

        self.show_bar(i, show=False)

        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=i: self.on_segment_click(i))

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

    def load_log_files(self, box_index):
        """모든 로그 파일을 로드하여 시간순으로 정렬된 로그 목록을 반환"""
        log_entries = []
        index = 0
        while True:
            log_file = os.path.join(self.history_dir, f"box_{box_index}_{index}.log")
            if not os.path.exists(log_file):
                break
            with open(log_file, 'r') as file:
                lines = file.readlines()
                for line in lines:
                    timestamp, value = line.strip().split(',')
                    log_entries.append((timestamp, value))
            index += 1
        log_entries.sort()
        return log_entries

    def show_history_graph(self, box_index):
        with self.history_lock:
            if self.history_window and self.history_window.winfo_exists():
                self.history_window.destroy()

            self.history_window = Toplevel(self.root)
            self.history_window.title(f"History - Box {box_index}")
            self.history_window.geometry("1200x800")
            self.history_window.attributes("-topmost", True)

            frame = Frame(self.history_window)
            frame.pack(fill="both", expand=True)

            figure = plt.Figure(figsize=(12, 8), dpi=100)
            ax = figure.add_subplot(111)

            log_entries = self.load_log_files(box_index)
            times, values = zip(*log_entries) if log_entries else ([], [])
            ax.plot(times, values, marker='o')
            ax.set_title('History')
            ax.set_xlabel('Time')
            ax.set_ylabel('Value')
            figure.autofmt_xdate()

            canvas = FigureCanvasTkAgg(figure, master=frame)
            canvas.draw()
            canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

            scrollbar = Scrollbar(frame, orient=HORIZONTAL, command=canvas.get_tk_widget().xview)
            scrollbar.pack(side="bottom", fill="x")
            canvas.get_tk_widget().config(xscrollcommand=scrollbar.set)

            mplcursors.cursor(ax)

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
                self.root.after(0, lambda: self.entries[i].config(state=DISABLED))
                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
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
            self.root.after(0, lambda: self.entries[i].config(state=NORMAL))
            self.update_circle_state([False, False, False, False], box_index=i)
            self.update_segment_display("    ", self.box_frames[i][1], box_index=i)
            self.show_bar(i, show=False)

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
                if self.connect_to_server(ip, client):
                    self.console.print(f"Reconnected to {ip}")
                else:
                    self.console.print(f"Failed to reconnect to {ip}. Exiting thread.")
                    stop_flag.set()
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
