# modbus_ui.py

import json
import os
import time
from tkinter import Frame, Canvas, StringVar, Entry, Button, Toplevel
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

        self.communication_interval = 0.2  # 200ms
        self.blink_interval = int((self.communication_interval / 1) * 1000)  # 200ms

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
            activebackground='black'
        )
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
        # 상태가 'normal'일 때만 가상 키보드 표시
        if entry['state'] == 'normal':
            self.on_focus_in(event, entry, placeholder)
            self.show_virtual_keyboard(entry)

    def create_modbus_box(self, index):
        box_frame = Frame(self.parent, highlightthickness=int(7 * SCALE_FACTOR))

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(
            inner_frame,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000000",
            highlightcolor="#000000"
        )
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR), fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, int(200 * SCALE_FACTOR), int(260 * SCALE_FACTOR), int(310 * SCALE_FACTOR), fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)
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

        self.box_states[index]["gas_type_var"].trace_add(
            "write",
            lambda *args, var=self.box_states[index]["gas_type_var"], idx=index: self.update_full_scale(var, idx)
        )

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=int(10 * SCALE_FACTOR), y=int(205 * SCALE_FACTOR))

        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        circle_items = []

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
            text="AL1",
            fill="#cccccc",
            anchor="e"
        )

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
            text="AL2",
            fill="#cccccc",
            anchor="e"
        )

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

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_data[box_index][0]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]["gas_type_text_id"], *position)
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

    def update_circle_state(self, states, box_index=0):
        box_canvas, circle_items, _, _, _ = self.box_data[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"modbus_{box_index}")

        if states[0]:
            outline_color = outline_colors[0]
        elif states[1]:
            outline_color = outline_colors[1]
        elif states[3]:
            outline_color = outline_colors[3]
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

    def update_segment_display(self, value, box_index=0, blink=False):
        box_canvas = self.box_data[box_index][0]
        value = value.zfill(4)
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.box_states[box_index]["previous_segment_display"] = value

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
                self.parent.after(0, lambda: self.entries[i].config(state="disabled", bg="#e0e0e0"))
                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
                self.blink_pwr(i)
                self.save_ip_settings()
            else:
                self.console.print(f"Failed to connect to {ip}")
                self.parent.after(0, lambda: self.update_circle_state([False, False, False, False], box_index=i))

    def disconnect(self, i):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            threading.Thread(target=self.disconnect_client, args=(ip, i)).start()

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
        self.parent.after(0, lambda: self.entries[i].config(state="normal", bg="white"))
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

                value_40001 = result_40001.registers[0]
                bit_6_on = bool(value_40001 & (1 << 6))
                bit_7_on = bool(value_40001 & (1 << 7))

                if bit_7_on:
                    top_blink = True
                    middle_blink = False
                    middle_fixed = True
                elif bit_6_on:
                    top_blink = False
                    middle_blink = True
                    middle_fixed = True
                else:
                    top_blink = False
                    middle_blink = False
                    middle_fixed = True

                self.ui_update_queue.put(('circle_state', box_index, [top_blink, middle_blink, middle_fixed, False]))

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
        self.parent.after(100, self.update_ui_from_queue)

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
        self.parent.after(0, lambda: self.entries[box_index].config(state="normal", bg="white"))
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
                threading.Thread(target=self.read_modbus_data, args=(ip, client, stop_flag, box_index)).start()
                self.parent.after(0, lambda: self.action_buttons[box_index].config(image=self.disconnect_image, relief='flat', borderwidth=0))
                self.parent.after(0, lambda: self.entries[box_index].config(state="disabled", bg="#e0e0e0"))
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
        def toggle_color():
            box_canvas = self.box_data[box_index][0]
            circle_items = self.box_data[box_index][1]
            if self.box_states[box_index]["pwr_blink_state"]:
                box_canvas.itemconfig(circle_items[2], fill="red", outline="red")
            else:
                box_canvas.itemconfig(circle_items[2], fill="green", outline="green")
            self.box_states[box_index]["pwr_blink_state"] = not self.box_states[box_index]["pwr_blink_state"]
            if self.ip_vars[box_index].get() in self.connected_clients:
                self.parent.after(self.blink_interval, toggle_color)

        toggle_color()

# 추가적으로 main 실행 부분이 필요할 수 있습니다.
# 예를 들어, 아래와 같은 코드로 실행할 수 있습니다.

if __name__ == "__main__":
    import tkinter as tk

    def alarm_callback(active, source):
        if active:
            print(f"Alarm active from {source}")
        else:
            print(f"Alarm cleared from {source}")

    root = tk.Tk()
    root.title("Modbus UI")
    num_boxes = 4
    gas_types = {
        "modbus_box_0": "ORG",
        "modbus_box_1": "ARF-T",
        "modbus_box_2": "HMDS",
        "modbus_box_3": "HC-100"
    }
    app = ModbusUI(root, num_boxes, gas_types, alarm_callback)
    root.mainloop()
