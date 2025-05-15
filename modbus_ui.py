#!/usr/bin/env python3
# coding: utf-8

import sys, time
import json
import os
import threading
import queue
from tkinter import Frame, Canvas, StringVar, Entry, Button, Toplevel, Tk, Label
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from rich.console import Console
from PIL import Image, ImageTk

# 외부 파일에서 임포트 (가정)
from common import SEGMENTS, BIT_TO_SEGMENT, create_segment_display, create_gradient_bar
from virtual_keyboard import VirtualKeyboard

SCALE_FACTOR = 1.65


class ModbusUI:
    SETTINGS_FILE = "modbus_settings.json"
    BASE = 40001
    UNIT = 1

    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG":    (int(115 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "ARF-T":  (int(107 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
        "HMDS":   (int(110 * SCALE_FACTOR), int(100 * SCALE_FACTOR)),
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
        self.box_frames = []
        self.box_data = []
        self.gradient_bar = create_gradient_bar(int(120 * SCALE_FACTOR), int(5 * SCALE_FACTOR))
        self.gas_types = gas_types

        # 연결 끊김 관련 관리
        self.disconnection_counts = [0] * num_boxes
        self.disconnection_labels = [None] * num_boxes
        self.auto_reconnect_failed = [False] * num_boxes
        self.reconnect_attempt_labels = [None] * num_boxes

        self.load_ip_settings(num_boxes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, "img/on.png")
        disconnect_image_path = os.path.join(script_dir, "img/off.png")

        self.connect_image = self.load_image(connect_image_path, (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR)))
        self.disconnect_image = self.load_image(disconnect_image_path, (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR)))

        for i in range(num_boxes):
            self.create_modbus_box(i)

        self.communication_interval = 0.2
        self.blink_interval = int(self.communication_interval * 1000)
        self.alarm_blink_interval = 1000

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

    def safe_read(self, client, reg):
        try:
            return client.read_holding_registers(reg - self.BASE, 1, slave=self.UNIT)
        except (ConnectionException, ModbusIOException):
            client.close()
            time.sleep(1)
            client.connect()
            return client.read_holding_registers(reg - self.BASE, 1, slave=self.UNIT)

    def safe_write(self, client, reg, val):
        try:
            return client.write_register(reg - self.BASE, val, slave=self.UNIT)
        except (ConnectionException, ModbusIOException):
            client.close()
            time.sleep(1)
            client.connect()
            return client.write_register(reg - self.BASE, val, slave=self.UNIT)

    def ip2regs(self, ip):
        a, b, c, d = map(int, ip.split('.'))
        return [(a << 8) | b, (c << 8) | d]

    # --- New Modbus command methods ---
    def read_version(self, box_index):
        ip = self.ip_vars[box_index].get()
        client = ModbusTcpClient(ip, port=502, timeout=3)
        if client.connect():
            rr = self.safe_read(client, self.BASE + 21)  # 40022
            ver = rr.registers[0] if not rr.isError() else None
            self.console.print(f"[Box {box_index}] Version: {ver}")
        client.close()

    def set_tftp(self, box_index, tftp_ip):
        ip = self.ip_vars[box_index].get()
        regs = self.ip2regs(tftp_ip)
        client = ModbusTcpClient(ip, port=502, timeout=3)
        if client.connect():
            wr = client.write_registers(self.BASE + 87, regs, slave=self.UNIT)  # 40088~40089
            status = "OK" if not wr.isError() else wr
            self.console.print(f"[Box {box_index}] TFTP IP set to {tftp_ip}: {status}")
        client.close()

    def upgrade(self, box_index):
        ip = self.ip_vars[box_index].get()
        client = ModbusTcpClient(ip, port=502, timeout=3)
        if not client.connect():
            return
        wr = self.safe_write(client, self.BASE + 90, 1)  # 40091
        if wr.isError():
            self.console.print(f"[Box {box_index}] Upgrade start failed: {wr}")
            client.close()
            return
        self.console.print(f"[Box {box_index}] Upgrade started. Polling status...")
        while True:
            rr_stat = self.safe_read(client, self.BASE + 22)  # 40023
            if rr_stat.isError():
                time.sleep(1)
                continue
            st = rr_stat.registers[0]
            done = bool(st & 0x0001)
            fail = bool(st & 0x0002)
            in_prog = bool(st & 0x0004)
            err_code = (st >> 8) & 0xFF

            rr_prog = self.safe_read(client, self.BASE + 23)  # 40024
            if rr_prog.isError():
                time.sleep(1)
                continue
            pv = rr_prog.registers[0]
            progress = pv & 0xFF
            remain = (pv >> 8) & 0xFF

            status = "완료" if done else "실패" if fail else "진행중" if in_prog else "대기"
            sys.stdout.write(f"\r[{status}] {progress:3d}% 남은시간 {remain:3d}s 에러코드 {err_code}")
            sys.stdout.flush()

            if done or fail:
                print()
                self.console.print(f"[Box {box_index}] Upgrade {'success' if done else 'failed'}")
                break
            time.sleep(1)
        client.close()

    def zero_cal(self, box_index):
        ip = self.ip_vars[box_index].get()
        client = ModbusTcpClient(ip, port=502, timeout=3)
        if client.connect():
            wr = self.safe_write(client, self.BASE + 91, 1)  # 40092
            status = "OK" if not wr.isError() else wr
            self.console.print(f"[Box {box_index}] Zero Calibration: {status}")
        client.close()
    # --- end new methods ---

    def add_ip_row(self, frame, ip_var, index):
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

        # 연결/해제 버튼
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
        self.entries.append(entry)

        # --- new command buttons ---
        Button(frame, text="Ver", command=lambda i=index: self.read_version(i)).grid(row=0, column=2, padx=2)
        Button(frame, text="TFTP", command=lambda i=index: self.set_tftp(i, ip_var.get())).grid(row=0, column=3, padx=2)
        Button(frame, text="Upg", command=lambda i=index: self.upgrade(i)).grid(row=0, column=4, padx=2)
        Button(frame, text="Cal", command=lambda i=index: self.zero_cal(i)).grid(row=0, column=5, padx=2)
        # --- end new buttons ---

    def show_virtual_keyboard(self, entry):
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    def create_modbus_box(self, index):
        """
        아날로그박스(캔버스+테두리+IP입력+알람램프 등) 생성
        """
        box_frame = Frame(self.parent, highlightthickness=7)
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

        # 윗부분 회색, 아랫부분 검정 영역
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

        create_segment_display(box_canvas)

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
            "alarm1_on": False,
            "alarm2_on": False,
            "alarm1_blinking": False,
            "alarm2_blinking": False,
            "alarm_border_blink": False,
            "border_blink_state": False,
            "gms1000_text_id": None
        })

        control_frame = Frame(box_canvas, bg="black")
        control_frame.place(x=int(10 * SCALE_FACTOR), y=int(210 * SCALE_FACTOR))

        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        disconnection_label = Label(
            control_frame,
            text=f"DC: {self.disconnection_counts[index]}",
            fg="white",
            bg="black",
            font=("Helvetica", int(10 * SCALE_FACTOR))
        )
        disconnection_label.grid(row=1, column=0, columnspan=2, pady=(2,0))
        self.disconnection_labels[index] = disconnection_label

        reconnect_label = Label(
            control_frame,
            text="Reconnect: 0/5",
            fg="yellow",
            bg="black",
            font=("Helvetica", int(10 * SCALE_FACTOR))
        )
        reconnect_label.grid(row=2, column=0, columnspan=2, pady=(2,0))
        self.reconnect_attempt_labels[index] = reconnect_label

        disconnection_label.grid_remove()
        reconnect_label.grid_remove()

        circle_al1 = box_canvas.create_oval(
            int(77 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(87 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        )
        box_canvas.create_text(
            int(95 * SCALE_FACTOR) - int(25 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="AL1",
            fill="#cccccc",
            anchor="e"
        )

        circle_al2 = box_canvas.create_oval(
            int(133 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(123 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        )
        box_canvas.create_text(
            int(140 * SCALE_FACTOR) - int(35 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="AL2",
            fill="#cccccc",
            anchor="e"
        )

        circle_pwr = box_canvas.create_oval(
            int(30 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(40 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
        )
        box_canvas.create_text(
            int(35 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
            int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            text="PWR",
            fill="#cccccc",
            anchor="center"
        )

        circle_fut = box_canvas.create_oval(
            int(171 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
            int(181 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
            int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)
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

        gms1000_text_id = box_canvas.create_text(
            int(80 * SCALE_FACTOR),
            int(270 * SCALE_FACTOR),
            text="GMS-1000",
            font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
            fill="#cccccc",
            anchor="center"
        )
        self.box_states[index]["gms1000_text_id"] = gms1000_text_id

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
        self.box_data.append((box_canvas,
                              [circle_al1, circle_al2, circle_pwr, circle_fut],
                              bar_canvas, bar_image, bar_item))

        self.show_bar(index, show=False)
        self.update_circle_state([False, False, False, False], box_index=index)

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
                t = threading.Thread(target=self.read_modbus_data, args=(ip, client, stop_flag, i), daemon=True)
                self.connected_clients[ip] = t
                t.start()
                self.console.print(f"Started data thread for {ip}")

                box_canvas = self.box_data[i][0]
                gms1000_id = self.box_states[i]["gms1000_text_id"]
                box_canvas.itemconfig(gms1000_id, state='hidden')

                self.disconnection_labels[i].grid()
                self.reconnect_attempt_labels[i].grid()

                self.parent.after(0, lambda: self.action_buttons[i].config(image=self.disconnect_image))
                self.parent.after(0, lambda: self.entries[i].config(state="disabled"))

                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
                self.blink_pwr(i)
                self.save_ip_settings()

                self.entries[i].event_generate("<FocusOut>")
            else:
                self.console.print(f"Failed to connect to {ip}")
                self.parent.after(0, lambda: self.update_circle_state([False, False, False, False], box_index=i))

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i, manual=True)
        else:
            threading.Thread(target=self.connect, args=(i,), daemon=True).start()

    def disconnect(self, i, manual=False):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            threading.Thread(target=self.disconnect_client, args=(ip, i, manual), daemon=True).start()

    def disconnect_client(self, ip, i, manual=False):
        self.stop_flags[ip].set()
        self.connected_clients[ip].join(timeout=5)
        if self.connected_clients[ip].is_alive():
            self.console.print(f"Thread for {ip} did not terminate in time.")
        self.clients[ip].close()
        self.console.print(f"Disconnected from {ip}")
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

        self.parent.after(0, lambda: self.action_buttons[i].config(image=self.connect_image))
        self.parent.after(0, lambda: self.entries[i].config(state="normal"))
        self.parent.after(0, lambda: self.box_frames[i].config(highlightthickness=1))
        self.save_ip_settings()

        if manual:
            box_canvas = self.box_data[i][0]
            gms1000_id = self.box_states[i]["gms1000_text_id"]
            box_canvas.itemconfig(gms1000_id, state='normal')
            self.disconnection_labels[i].grid_remove()
            self.reconnect_attempt_labels[i].grid_remove()

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        start_address = self.BASE - 1
        num_registers = 11
        while not stop_flag.is_set():
            try:
                if client is None or not client.is_socket_open():
                    raise ConnectionException("Socket is closed")

                response = client.read_holding_registers(start_address, num_registers)
                if response.isError():
                    raise ModbusIOException(f"Error reading from {ip}")

                raw = response.registers
                value_40001 = raw[0]
                value_40005 = raw[4]
                value_40007 = raw[7]
                value_40011 = raw[10]

                bit_6 = bool(value_40001 & (1 << 6))
                bit_7 = bool(value_40001 & (1 << 7))
                self.box_states[box_index]["alarm1_on"] = bit_6
                self.box_states[box_index]["alarm2_on"] = bit_7
                self.ui_update_queue.put(('alarm_check', box_index))

                bits = [bool(value_40007 & (1 << n)) for n in range(4)]
                if not any(bits):
                    self.data_queue.put((box_index, str(value_40005), False))
                else:
                    disp = ""
                    for idx, flag in enumerate(bits):
                        if flag:
                            disp = BIT_TO_SEGMENT[idx]
                            break
                    disp = disp.ljust(4)
                    blink = 'E' in disp
                    self.data_queue.put((box_index, disp, blink))
                    self.ui_update_queue.put(('circle_state', box_index, [False, False, True, blink]))

                self.ui_update_queue.put(('bar', box_index, value_40011))
                time.sleep(self.communication_interval)

            except Exception as e:
                self.console.print(f"Connection to {ip} lost: {e}")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break

    def update_circle_state(self, states, box_index=0):
        canvas, items, _, _, _ = self.box_data[box_index]
        on_colors = ['red', 'red', 'green', 'yellow']
        off_colors = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        for i, st in enumerate(states):
            color = on_colors[i] if st else off_colors[i]
            canvas.itemconfig(items[i], fill=color, outline=color)
        self.alarm_callback(any(states[:2]), f"modbus_{box_index}")

    def update_segment_display(self, value, box_index=0, blink=False):
        canvas = self.box_data[box_index][0]
        val = value.zfill(4)
        prev = self.box_states[box_index]["previous_segment_display"]
        if val != prev:
            self.box_states[box_index]["previous_segment_display"] = val
        leading = True
        for idx, ch in enumerate(val):
            seg = SEGMENTS[' '] if (leading and ch=='0' and idx<3) else SEGMENTS.get(ch, SEGMENTS[' '])
            if seg and seg==' ':
                leading = True
            else:
                leading = False
            if blink and self.box_states[box_index]["blink_state"]:
                seg = SEGMENTS[' ']
            for j, bit in enumerate(seg):
                tag = f'segment_{idx}_{chr(97+j)}'
                color = '#fc0c0c' if bit=='1' else '#424242'
                if canvas.find_withtag(tag):
                    canvas.itemconfig(tag, fill=color)
        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def update_bar(self, value, box_index):
        _, _, bar_canvas, _, bar_item = self.box_data[box_index]
        perc = value / 100.0
        length = int(153 * SCALE_FACTOR * perc)
        cropped = self.gradient_bar.crop((0,0,length,int(5*SCALE_FACTOR)))
        img = ImageTk.PhotoImage(cropped)
        bar_canvas.itemconfig(bar_item, image=img)
        bar_canvas.bar_image = img

    def show_bar(self, box_index, show):
        canvas = self.box_data[box_index][2]
        item = self.box_data[box_index][4]
        canvas.itemconfig(item, state='normal' if show else 'hidden')

    def connect_to_server(self, ip, client):
        for attempt in range(5):
            if client.connect():
                self.console.print(f"Connected to {ip}")
                return True
            time.sleep(2)
        return False

    def start_data_processing_thread(self):
        threading.Thread(target=self.process_data, daemon=True).start()

    def process_data(self):
        while True:
            try:
                box_index, val, blink = self.data_queue.get(timeout=1)
                self.ui_update_queue.put(('segment_display', box_index, val, blink))
            except queue.Empty:
                continue

    def schedule_ui_update(self):
        self.parent.after(100, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        try:
            while not self.ui_update_queue.empty():
                item = self.ui_update_queue.get_nowait()
                cmd = item[0]
                if cmd == 'circle_state':
                    _, bi, sts = item; self.update_circle_state(sts, bi)
                elif cmd == 'bar':
                    _, bi, v = item; self.update_bar(v, bi)
                elif cmd == 'segment_display':
                    _, bi, v, b = item; self.update_segment_display(v, bi, b)
                elif cmd == 'alarm_check':
                    _, bi = item; self.check_alarms(bi)
        except queue.Empty:
            pass
        finally:
            self.schedule_ui_update()

    def check_click(self, event):
        pass

    def handle_disconnection(self, box_index):
        self.disconnection_counts[box_index] += 1
        self.disconnection_labels[box_index].config(text=f"DC: {self.disconnection_counts[box_index]}")
        self.ui_update_queue.put(('circle_state', box_index, [False, False, False, False]))
        self.ui_update_queue.put(('segment_display', box_index, "    ", False))
        self.ui_update_queue.put(('bar', box_index, 0))
        self.parent.after(0, lambda: self.action_buttons[box_index].config(image=self.connect_image))
        self.parent.after(0, lambda: self.entries[box_index].config(state="normal"))
        self.parent.after(0, lambda: self.box_frames[box_index].config(highlightthickness=1))
        self.box_states[box_index]["pwr_blinking"] = False
        box_canvas, items, _, _, _ = self.box_data[box_index]
        box_canvas.itemconfig(items[2], fill="#e0fbba", outline="#e0fbba")
        self.console.print(f"PWR lamp reset for box {box_index}")

    def reconnect(self, ip, client, stop_flag, box_index):
        retries = 0
        while not stop_flag.is_set() and retries < 5:
            time.sleep(2)
            retries += 1
            self.parent.after(0, lambda idx=box_index, r=retries: self.reconnect_attempt_labels[idx].config(text=f"Reconnect: {r}/5"))
            if client.connect():
                stop_flag.clear()
                threading.Thread(target=self.read_modbus_data, args=(ip,client,stop_flag,box_index), daemon=True).start()
                self.parent.after(0, lambda: self.action_buttons[box_index].config(image=self.disconnect_image))
                self.parent.after(0, lambda: self.entries[box_index].config(state="disabled"))
                self.box_frames[box_index].config(highlightthickness=0)
                self.ui_update_queue.put(('circle_state', box_index, [False, False, True, False]))
                self.blink_pwr(box_index)
                self.show_bar(box_index, show=True)
                self.parent.after(0, lambda idx=box_index: self.reconnect_attempt_labels[idx].config(text="Reconnect: OK"))
                return
        self.auto_reconnect_failed[box_index] = True
        self.parent.after(0, lambda idx=box_index: self.reconnect_attempt_labels[idx].config(text="Reconnect: Failed"))
        self.disconnect_client(ip, box_index, manual=False)

    def save_ip_settings(self):
        with open(self.SETTINGS_FILE, 'w') as f:
            json.dump([var.get() for var in self.ip_vars], f)

    def blink_pwr(self, box_index):
        if self.box_states[box_index]["pwr_blinking"]:
            return
        self.box_states[box_index]["pwr_blinking"] = True

        def toggle():
            if self.ip_vars[box_index].get() not in self.connected_clients:
                canvas, items, *_ = self.box_data[box_index]
                canvas.itemconfig(items[2], fill="#e0fbba", outline="#e0fbba")
                self.box_states[box_index]["pwr_blinking"] = False
                return
            canvas, items, *_ = self.box_data[box_index]
            state = self.box_states[box_index]["pwr_blink_state"]
            color = "green" if not state else "red"
            canvas.itemconfig(items[2], fill=color, outline=color)
            self.box_states[box_index]["pwr_blink_state"] = not state
            self.parent.after(self.blink_interval, toggle)

        toggle()

    def check_alarms(self, box_index):
        a1 = self.box_states[box_index]["alarm1_on"]
        a2 = self.box_states[box_index]["alarm2_on"]
        if a2:
            self.box_states[box_index]["alarm2_blinking"] = True
            self.set_alarm_lamp(box_index, True, False, True, True)
            self.box_states[box_index]["alarm_border_blink"] = True
            self.blink_alarms(box_index)
        elif a1:
            self.box_states[box_index]["alarm1_blinking"] = True
            self.set_alarm_lamp(box_index, True, True, False, False)
            self.box_states[box_index]["alarm_border_blink"] = True
            self.blink_alarms(box_index)
        else:
            self.set_alarm_lamp(box_index, False, False, False, False)
            canvas = self.box_data[box_index][0]
            canvas.config(highlightbackground="#000000")
            self.box_states[box_index]["border_blink_state"] = False

    def set_alarm_lamp(self, box_index, a1_on, b1, a2_on, b2):
        canvas, items, *_ = self.box_data[box_index]
        # AL1
        canvas.itemconfig(items[0], fill="red" if a1_on and not b1 else "#fdc8c8")
        # AL2
        canvas.itemconfig(items[1], fill="red" if a2_on and not b2 else "#fdc8c8")

    def blink_alarms(self, box_index):
        if not (self.box_states[box_index]["alarm1_blinking"] or self.box_states[box_index]["alarm2_blinking"] or self.box_states[box_index]["alarm_border_blink"]):
            return
        canvas = self.box_data[box_index][0]
        state = self.box_states[box_index]["border_blink_state"]
        self.box_states[box_index]["border_blink_state"] = not state
        canvas.config(highlightbackground="#ff0000" if not state else "#000000")

        for idx in (0,1):
            if self.box_states[box_index][f"alarm{idx+1}_blinking"]:
                fill = canvas.itemcget(self.box_data[box_index][1][idx], "fill")
                canvas.itemconfig(self.box_data[box_index][1][idx], fill="#fdc8c8" if fill=="red" else "red")

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

    row, col = 0, 0
    max_col = 2
    for i, frame in enumerate(modbus_ui.box_frames):
        frame.grid(row=row, column=col, padx=10, pady=10)
        col += 1
        if col >= max_col:
            col = 0
            row += 1

    root.mainloop()


if __name__ == "__main__":
    main()
