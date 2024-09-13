# modbus_ui.py

import asyncio
import json
import os
import time
from tkinter import Frame, Canvas, StringVar, Entry, Button, Toplevel, Label, messagebox
import threading
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException
from rich.console import Console
from PIL import Image, ImageTk
from common import SEGMENTS, BIT_TO_SEGMENT, create_segment_display, create_gradient_bar
from virtual_keyboard import VirtualKeyboard

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

    def __init__(self, root, num_boxes, gas_types, alarm_callback):
        self.root = root
        self.alarm_callback = alarm_callback
        self.virtual_keyboard = VirtualKeyboard(root)
        self.ip_vars = [StringVar() for _ in range(num_boxes)]
        self.entries = []
        self.action_buttons = []
        self.clients = {}
        self.box_indices = {}
        self.console = Console()
        self.box_states = []
        self.graph_windows = [None for _ in range(num_boxes)]
        self.history_window = None
        self.history_lock = threading.Lock()
        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0)
        self.row_frames = []
        self.box_frames = []
        self.gradient_bar = create_gradient_bar(int(120 * SCALE_FACTOR), int(5 * SCALE_FACTOR))
        self.history_dir = "history_logs"
        self.gas_types = gas_types

        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        # IP 설정 로드
        self.load_ip_settings(num_boxes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, "img/on.png")
        disconnect_image_path = os.path.join(script_dir, "img/off.png")

        self.connect_image = self.load_image(connect_image_path, (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR)))
        self.disconnect_image = self.load_image(disconnect_image_path, (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR)))

        for i in range(num_boxes):
            self.create_modbus_box(i)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.start_event_loop, daemon=True).start()

        self.root.after(100, self.update_ui_from_queue)

        self.root.bind("<Button-1>", self.check_click)

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
        entry = Entry(frame, textvariable=ip_var, width=int(12 * SCALE_FACTOR), highlightthickness=0)
        placeholder_text = f"{index + 1}. IP를 입력해주세요."
        if ip_var.get() == '':
            entry.insert(0, placeholder_text)
            entry.config(fg="grey")
        else:
            entry.config(fg="black")

        entry.bind("<FocusIn>", lambda event, e=entry, p=placeholder_text: self.on_focus_in(event, e, p))
        entry.bind("<FocusOut>", lambda event, e=entry, p=placeholder_text: self.on_focus_out(event, e, p))
        entry.bind("<Button-1>", lambda event, e=entry, p=placeholder_text: self.on_entry_click(event, e, p))
        entry.grid(row=0, column=0)
        self.entries.append(entry)

        action_button = Button(frame, image=self.connect_image, command=lambda i=index: self.toggle_connection(i),
                               width=int(60 * SCALE_FACTOR), height=int(40 * SCALE_FACTOR), bd=0, highlightthickness=0, borderwidth=0, relief='flat', bg='black', activebackground='black')
        action_button.grid(row=0, column=1)
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
        max_boxes_per_row = 6
        row = index // max_boxes_per_row
        col = index % max_boxes_per_row

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0, sticky="w")
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame, highlightthickness=int(2.5 * SCALE_FACTOR))
        box_frame.grid(row=0, column=col)

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=int(2.5 * SCALE_FACTOR), pady=int(2.5 * SCALE_FACTOR))

        box_canvas = Canvas(inner_frame, width=int(150 * SCALE_FACTOR), height=int(300 * SCALE_FACTOR), highlightthickness=int(3 * SCALE_FACTOR), highlightbackground="#000000", highlightcolor="#000000")
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR), fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, int(200 * SCALE_FACTOR), int(260 * SCALE_FACTOR), int(310 * SCALE_FACTOR), fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None,
            "pwr_blink_state": False,
            "gas_type_var": StringVar(value=self.gas_types.get(f"modbus_box_{index}", "ORG")),
            "gas_type_text_id": None,
            "full_scale": self.GAS_FULL_SCALE[self.gas_types.get(f"modbus_box_{index}", "ORG")]
        })

        self.box_states[index]["gas_type_var"].trace_add("write", lambda *args, var=self.box_states[index]["gas_type_var"], idx=index: self.update_full_scale(var, idx))

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=int(10 * SCALE_FACTOR), y=int(205 * SCALE_FACTOR))

        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        circle_items = []

        circle_items.append(box_canvas.create_oval(int(133 * SCALE_FACTOR) - int(30 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(123 * SCALE_FACTOR) - int(30 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(95 * SCALE_FACTOR) - int(25 * SCALE_FACTOR), int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(int(77 * SCALE_FACTOR) - int(20 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(87 * SCALE_FACTOR) - int(20 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(140 * SCALE_FACTOR) - int(35 * SCALE_FACTOR), int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(int(30 * SCALE_FACTOR) - int(10 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(40 * SCALE_FACTOR) - int(10 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(35 * SCALE_FACTOR) - int(10 * SCALE_FACTOR), int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(int(171 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR), int(181 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(175 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), int(217 * SCALE_FACTOR) - int(40 * SCALE_FACTOR), text="FUT", fill="#cccccc", anchor="n")

        gas_type_var = self.box_states[index]["gas_type_var"]
        gas_type_text_id = box_canvas.create_text(*self.GAS_TYPE_POSITIONS[gas_type_var.get()], text=gas_type_var.get(), font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")
        self.box_states[index]["gas_type_text_id"] = gas_type_text_id

        box_canvas.create_text(int(80 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="GMS-1000", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(int(80 * SCALE_FACTOR), int(295 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD", font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")

        bar_canvas = Canvas(box_canvas, width=int(120 * SCALE_FACTOR), height=int(5 * SCALE_FACTOR), bg="white", highlightthickness=0)
        bar_canvas.place(x=int(18.5 * SCALE_FACTOR), y=int(75 * SCALE_FACTOR))

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
        self.alarm_callback(alarm_active, box_index)

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
        value = value.zfill(4)  # 네 자리로 맞추기
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.record_history(box_index, value)
            self.box_states[box_index]["previous_segment_display"] = value

        # 각 자리의 숫자를 순차적으로 업데이트
        leading_zero = True
        for index in range(len(value)):
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
                segment_tag = f'segment_{index}_{chr(97 + j)}'

                if box_canvas.segment_canvas.find_withtag(segment_tag):
                    box_canvas.segment_canvas.itemconfig(segment_tag, fill=color)

        # 블링크 상태 업데이트
        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def record_history(self, box_index, value):
        if value.strip():
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            log_line = f"{timestamp},{value}\n"
            log_file_index = self.get_log_file_index(box_index)
            log_file = os.path.join(self.history_dir, f"box_{box_index}_{log_file_index}.log")

            threading.Thread(target=self.async_write_log, args=(log_file, log_line)).start()

    def async_write_log(self, log_file, log_line):
        try:
            with open(log_file, 'a') as file:
                file.write(log_line)
        except IOError as e:
            self.console.print(f"Error writing log file: {e}")

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

        figure = plt.Figure(figsize=(12 * SCALE_FACTOR), dpi=100)
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
        if self.ip_vars[i].get() in self.clients:
            asyncio.run_coroutine_threadsafe(self.disconnect(i), self.loop)
        else:
            asyncio.run_coroutine_threadsafe(self.connect(i), self.loop)

    async def connect(self, i):
        ip = self.ip_vars[i].get()
        if ip and ip not in self.clients:
            try:
                client = AsyncModbusTcpClient(ip, port=502)
                await client.connect()
                self.clients[ip] = client
                self.box_indices[ip] = i
                self.root.after(0, lambda: self.action_buttons[i].config(image=self.disconnect_image, relief='flat', borderwidth=0))
                self.root.after(0, lambda: self.entries[i].config(state="disabled"))
                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
                self.blink_pwr(i)
                self.save_ip_settings()
                asyncio.run_coroutine_threadsafe(self.read_modbus_data(ip, client, i), self.loop)
            except Exception as e:
                self.console.print(f"Failed to connect to {ip}: {e}")

    async def disconnect(self, i):
        ip = self.ip_vars[i].get()
        if ip in self.clients:
            client = self.clients[ip]
            await client.close()
            del self.clients[ip]
            del self.box_indices[ip]
            self.reset_ui_elements(i)
            self.root.after(0, lambda: self.action_buttons[i].config(image=self.connect_image, relief='flat', borderwidth=0))
            self.root.after(0, lambda: self.entries[i].config(state="normal"))
            self.save_ip_settings()

    def reset_ui_elements(self, box_index):
        self.update_circle_state([False, False, False, False], box_index=box_index)
        self.update_segment_display("    ", self.box_frames[box_index][1], box_index=box_index)
        self.show_bar(box_index, show=False)
        self.console.print(f"Reset UI elements for box {box_index}")

    async def read_modbus_data(self, ip, client, box_index):
        interval = 0.2  # 폴링 주기를 200ms로 설정
        while ip in self.clients:
            try:
                if client.connected:
                    address_40001 = 40001 - 1
                    address_40005 = 40005 - 1
                    address_40007 = 40008 - 1
                    address_40011 = 40011 - 1
                    count = 1

                    result_40001 = await client.read_holding_registers(address_40001, count)
                    result_40005 = await client.read_holding_registers(address_40005, count)
                    result_40007 = await client.read_holding_registers(address_40007, count)
                    result_40011 = await client.read_holding_registers(address_40011, count)

                    if not result_40001.isError():
                        value_40001 = result_40001.registers[0]
                        bit_6_on = bool(value_40001 & (1 << 6))
                        bit_7_on = bool(value_40001 & (1 << 7))

                        if bit_7_on:
                            top_blink = True
                            middle_blink = False
                            middle_fixed = True
                            self.record_history(box_index, 'A2')
                        elif bit_6_on:
                            top_blink = False
                            middle_blink = True
                            middle_fixed = True
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
                                formatted_value = f"{value_40005}"
                                self.update_segment_display(formatted_value, self.box_frames[box_index][1], blink=False, box_index=box_index)
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
                                    self.update_segment_display(error_display, self.box_frames[box_index][1], blink=True, box_index=box_index)
                                    self.update_circle_state([False, False, True, self.box_states[box_index]["blink_state"]], box_index=box_index)
                                else:
                                    self.box_states[box_index]["blinking_error"] = False
                                    self.update_segment_display(error_display, self.box_frames[box_index][1], blink=False, box_index=box_index)
                                    self.update_circle_state([False, False, True, False], box_index=box_index)
                    if not result_40011.isError():
                        value_40011 = result_40011.registers[0]
                        self.update_bar(value_40011, self.box_frames[box_index][3], self.box_frames[box_index][5])

                    await asyncio.sleep(interval)
                else:
                    raise ConnectionException("Connection lost")
            except Exception as e:
                self.console.print(f"Error reading data from {ip}: {e}")
                self.reset_ui_elements(box_index)
                break

    def update_bar(self, value, bar_canvas, bar_item):
        percentage = value / 100.0
        bar_length = int(153 * SCALE_FACTOR * percentage)

        cropped_image = self.gradient_bar.crop((0, 0, bar_length, int(5 * SCALE_FACTOR)))
        bar_image = ImageTk.PhotoImage(cropped_image)
        bar_canvas.itemconfig(bar_item, image=bar_image)
        bar_canvas.bar_image = bar_image

    def show_bar(self, box_index, show):
        bar_canvas, _, bar_item = self.box_frames[box_index][3:6]
        if show:
            bar_canvas.itemconfig(bar_item, state='normal')
        else:
            bar_canvas.itemconfig(bar_item, state='hidden')

    def save_ip_settings(self):
        ip_settings = [ip_var.get() for ip_var in self.ip_vars]
        with open(self.SETTINGS_FILE, 'w') as file:
            json.dump(ip_settings, file)

    def start_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def update_ui_from_queue(self):
        # 필요 시 UI 업데이트 로직 추가
        self.root.after(100, self.update_ui_from_queue)

    def check_click(self, event):
        if hasattr(self, 'history_frame') and self.history_frame.winfo_exists():
            widget = event.widget
            if widget != self.history_frame and not self.history_frame.winfo_containing(event.x_root, event.y_root):
                self.hide_history(event)

    def blink_pwr(self, box_index):
        def toggle_color():
            if self.box_states[box_index]["pwr_blink_state"]:
                self.box_frames[box_index][1].itemconfig(self.box_frames[box_index][2][2], fill="blue", outline="blue")
            else:
                self.box_frames[box_index][1].itemconfig(self.box_frames[box_index][2][2], fill="green", outline="green")
            self.box_states[box_index]["pwr_blink_state"] = not self.box_states[box_index]["pwr_blink_state"]
            if self.ip_vars[box_index].get() in self.clients:
                self.root.after(600, toggle_color)

        toggle_color()
