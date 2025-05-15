#!/usr/bin/env python3
# coding: utf-8

import sys
import time
import json
import os
import queue
import threading
from tkinter import (
    Frame, Canvas, StringVar, Entry, Button, Toplevel, Tk, Label,
    simpledialog, messagebox
)
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from rich.console import Console
from PIL import Image, ImageTk

# 외부 파일에서 임포트 (가정)
from common import SEGMENTS, BIT_TO_SEGMENT, create_segment_display, create_gradient_bar
from virtual_keyboard import VirtualKeyboard

SCALE_FACTOR = 1.65
BASE = 40001
UNIT = 1

def off(reg):
    return reg - BASE

def ip2regs(ip):
    a, b, c, d = map(int, ip.split('.'))
    return [(a << 8) | b, (c << 8) | d]

class ModbusUI:
    SETTINGS_FILE = "modbus_settings.json"
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
        self.gradient_bar = create_gradient_bar(
            int(120 * SCALE_FACTOR), int(5 * SCALE_FACTOR)
        )
        self.gas_types = gas_types

        # 연결 끊김 관련 관리
        self.disconnection_counts = [0] * num_boxes
        self.disconnection_labels = [None] * num_boxes
        self.auto_reconnect_failed = [False] * num_boxes
        self.reconnect_attempt_labels = [None] * num_boxes

        self.load_ip_settings(num_boxes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.connect_image = self.load_image(
            os.path.join(script_dir, "img/on.png"),
            (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR))
        )
        self.disconnect_image = self.load_image(
            os.path.join(script_dir, "img/off.png"),
            (int(50 * SCALE_FACTOR), int(70 * SCALE_FACTOR))
        )

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
            with open(self.SETTINGS_FILE, 'r') as f:
                ip_settings = json.load(f)
            for i in range(min(num_boxes, len(ip_settings))):
                self.ip_vars[i].set(ip_settings[i])

    def save_ip_settings(self):
        ip_settings = [ip.get() for ip in self.ip_vars]
        with open(self.SETTINGS_FILE, 'w') as f:
            json.dump(ip_settings, f)

    def load_image(self, path, size):
        img = Image.open(path).convert("RGBA")
        img.thumbnail(size, Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def show_virtual_keyboard(self, entry):
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    def add_ip_row(self, frame, ip_var, index):
        entry_border = Frame(frame, bg="#4a4a4a", bd=1, relief='solid')
        entry_border.grid(row=0, column=0, padx=(0,0), pady=5)

        entry = Entry(
            entry_border, textvariable=ip_var,
            width=int(7 * SCALE_FACTOR), bd=0, relief='flat',
            bg="#2e2e2e", fg="white", insertbackground="white",
            font=("Helvetica", int(10 * SCALE_FACTOR)),
            justify='center'
        )
        entry.pack(padx=2, pady=3)

        placeholder = f"{index+1}. IP를 입력해주세요."
        if not ip_var.get():
            entry.insert(0, placeholder)
            entry.config(fg="#a9a9a9")
        else:
            entry.config(fg="white")

        def on_focus_in(ev, e=entry, p=placeholder):
            if e.get() == p:
                e.delete(0, "end")
                e.config(fg="white")
            entry_border.config(bg="#1e90ff")
            e.config(bg="#3a3a3a")

        def on_focus_out(ev, e=entry, p=placeholder):
            if not e.get():
                e.insert(0, p)
                e.config(fg="#a9a9a9")
            entry_border.config(bg="#4a4a4a")
            e.config(bg="#2e2e2e")

        def on_click(ev, e=entry, p=placeholder):
            on_focus_in(ev,e,p)
            self.show_virtual_keyboard(e)

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry.bind("<Button-1>", on_click)

        btn = Button(
            frame, image=self.connect_image,
            command=lambda i=index: self.toggle_connection(i),
            width=int(60 * SCALE_FACTOR), height=int(40 * SCALE_FACTOR),
            bd=0, relief='flat', bg='black', activebackground='black',
            cursor="hand2"
        )
        btn.grid(row=0, column=1)
        self.entries.append(entry)
        self.action_buttons.append(btn)

    def create_modbus_box(self, index):
        box_frame = Frame(self.parent, highlightthickness=7)
        inner = Frame(box_frame)
        inner.pack()
        canvas = Canvas(
            inner,
            width=int(150 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=int(3 * SCALE_FACTOR),
            highlightbackground="#000", bg="#1e1e1e"
        )
        canvas.pack()

        # 배경
        canvas.create_rectangle(0, 0, int(160*SCALE_FACTOR),
                                int(200*SCALE_FACTOR),
                                fill='grey', outline='grey', tags='border')
        canvas.create_rectangle(0, int(200*SCALE_FACTOR), int(260*SCALE_FACTOR),
                                int(310*SCALE_FACTOR),
                                fill='black', outline='grey', tags='border')

        create_segment_display(canvas)

        # 상태 dict
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_segment_display": None,
            "pwr_blink_state": False,
            "pwr_blinking": False,
            "alarm1_on": False,
            "alarm2_on": False,
            "alarm1_blinking": False,
            "alarm2_blinking": False,
            "alarm_border_blink": False,
            "border_blink_state": False,
            "gas_type_var": StringVar(value=self.gas_types.get(f"modbus_box_{index}", "ORG")),
            "gas_type_text_id": None,
            "full_scale": self.GAS_FULL_SCALE[self.gas_types.get(f"modbus_box_{index}", "ORG")],
            "gms1000_text_id": None
        })

        # IP + 버튼
        control = Frame(canvas, bg="black")
        control.place(x=int(10*SCALE_FACTOR), y=int(210*SCALE_FACTOR))
        self.add_ip_row(control, self.ip_vars[index], index)

        # DC/Reconnect 라벨
        lbl_dc = Label(control, text="DC: 0", fg="white", bg="black",
                       font=("Helvetica",int(10*SCALE_FACTOR)))
        lbl_rc = Label(control, text="Reconnect: 0/5", fg="yellow", bg="black",
                       font=("Helvetica",int(10*SCALE_FACTOR)))
        lbl_dc.grid(row=1,column=0,columnspan=2,pady=(2,0))
        lbl_rc.grid(row=2,column=0,columnspan=2,pady=(2,0))
        lbl_dc.grid_remove(); lbl_rc.grid_remove()
        self.disconnection_labels[index] = lbl_dc
        self.reconnect_attempt_labels[index] = lbl_rc

        # 알람/전원/FUT 램프
        circles = []
        # AL1, AL2, PWR, FUT 위치
        offsets = [
            (77-20, 200-32, 87-20, 190-32),  # AL1
            (133-30,200-32,123-30,190-32),  # AL2
            (30-10, 200-32, 40-10,190-32),  # PWR
            (171-40,200-32,181-40,190-32)   # FUT
        ]
        for off_coords in offsets:
            x1,y1,x2,y2 = [int(v*SCALE_FACTOR) for v in off_coords]
            c = canvas.create_oval(x1,y1,x2,y2)
            circles.append(c)
        self.box_data.append((canvas, circles))

        # GAS 타입 텍스트
        gv = self.box_states[index]["gas_type_var"]
        tid = canvas.create_text(
            *self.GAS_TYPE_POSITIONS[gv.get()],
            text=gv.get(),
            font=("Helvetica",int(16*SCALE_FACTOR),"bold"),
            fill="#cccccc", anchor="center"
        )
        self.box_states[index]["gas_type_text_id"] = tid

        # GMS-1000, 회사명
        gtid = canvas.create_text(
            int(80*SCALE_FACTOR), int(270*SCALE_FACTOR),
            text="GMS-1000",
            font=("Helvetica",int(16*SCALE_FACTOR),"bold"),
            fill="#cccccc", anchor="center"
        )
        self.box_states[index]["gms1000_text_id"] = gtid

        canvas.create_text(
            int(80*SCALE_FACTOR), int(295*SCALE_FACTOR),
            text="GDS ENGINEERING CO.,LTD",
            font=("Helvetica",int(7*SCALE_FACTOR),"bold"),
            fill="#cccccc", anchor="center"
        )

        # Bar
        bar_cv = Canvas(
            canvas, width=int(120*SCALE_FACTOR),
            height=int(5*SCALE_FACTOR), bg="white", highlightthickness=0
        )
        bar_cv.place(x=int(18.5*SCALE_FACTOR), y=int(75*SCALE_FACTOR))
        bar_img = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_cv.create_image(0,0,anchor='nw',image=bar_img)

        self.box_data[index] += (bar_cv, bar_img, bar_item)

        # 기능 버튼
        func_frame = Frame(control, bg="black")
        func_frame.grid(row=3, column=0, columnspan=2, pady=(5,0))
        funcs = [
            ("버전 읽기", self.read_version),
            ("TFTP 설정", self.set_tftp),
            ("업그레이드", self.upgrade),
            ("제로 캘", self.zero_cal),
        ]
        for idx, (txt, fn) in enumerate(funcs):
            b = Button(func_frame, text=txt,
                       command=lambda i=index, f=fn: f(i),
                       width=8)
            b.grid(row=idx//2, column=idx%2, padx=2, pady=2)

        # 초기 UI
        self.show_bar(index, False)
        self.update_circle_state([False]*4, box_index=index)

        self.box_frames.append(box_frame)

    # --- CLI 기능 메서드 ---
    def get_client(self, index):
        ip = self.ip_vars[index].get()
        if not ip:
            messagebox.showwarning("IP 없음", "IP를 입력해주세요.")
            return None
        cli = ModbusTcpClient(ip, port=502, timeout=5)
        if not cli.connect():
            messagebox.showerror("연결 실패", f"{ip}에 연결할 수 없습니다.")
            return None
        return cli

    def read_version(self, index):
        cli = self.get_client(index)
        if not cli: return
        rr = cli.read_holding_registers(off(40022), 1, slave=UNIT)
        if rr.isError():
            messagebox.showerror("버전 읽기 오류", str(rr))
        else:
            messagebox.showinfo("버전", f"{rr.registers[0]}")
        cli.close()

    def set_tftp(self, index):
        tftp_ip = simpledialog.askstring("TFTP IP", "설정할 TFTP IP를 입력하세요:")
        if not tftp_ip: return
        regs = ip2regs(tftp_ip)
        cli = self.get_client(index)
        if not cli: return
        wr = cli.write_registers(off(40088), regs, slave=UNIT)
        if wr.isError():
            messagebox.showerror("TFTP 설정 실패", str(wr))
        else:
            messagebox.showinfo("TFTP 설정", "성공")
        cli.close()

    def upgrade(self, index):
        cli = self.get_client(index)
        if not cli: return
        wr = cli.write_register(off(40091), 1, slave=UNIT)
        if wr.isError():
            messagebox.showerror("업그레이드 시작 실패", str(wr))
            cli.close()
            return
        # 상태 읽기
        rr = cli.read_holding_registers(off(40023), 1, slave=UNIT)
        if rr.isError():
            messagebox.showwarning("업그레이드 상태", "진행 상태 읽기 실패")
        else:
            st = rr.registers[0]
            done = bool(st & 0x0001)
            messagebox.showinfo("업그레이드 상태", "완료" if done else "진행중/실패")
        cli.close()

    def zero_cal(self, index):
        cli = self.get_client(index)
        if not cli: return
        wr = cli.write_register(off(40092), 1, slave=UNIT)
        if wr.isError():
            messagebox.showerror("제로 캘 실패", str(wr))
        else:
            messagebox.showinfo("제로 캘", "성공")
        cli.close()

    # --- 데이터 처리 & UI 업데이트 ---
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
                t = item[0]
                if t == 'circle_state':
                    _, box_index, states = item
                    self.update_circle_state(states, box_index)
                elif t == 'bar':
                    _, box_index, val = item
                    self.update_bar(val, box_index)
                elif t == 'segment_display':
                    _, box_index, val, blink = item
                    self.update_segment_display(val, box_index, blink)
                elif t == 'alarm_check':
                    _, box_index = item
                    self.check_alarms(box_index)
        except queue.Empty:
            pass
        finally:
            self.schedule_ui_update()

    def toggle_connection(self, i):
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

        if not ip or ip in self.connected_clients:
            return

        client = ModbusTcpClient(ip, port=502, timeout=3)
        if not self.connect_to_server(ip, client):
            self.console.print(f"Failed to connect to {ip}")
            self.update_circle_state([False]*4, i)
            return

        stop_flag = threading.Event()
        self.stop_flags[ip] = stop_flag
        self.clients[ip] = client

        th = threading.Thread(
            target=self.read_modbus_data,
            args=(ip, client, stop_flag, i),
            daemon=True
        )
        self.connected_clients[ip] = th
        th.start()
        self.console.print(f"Started data thread for {ip}")

        # UI 변경
        canvas, circles, _, _, _ = self.box_data[i]
        canvas.itemconfig(self.box_states[i]["gms1000_text_id"], state='hidden')
        self.disconnection_labels[i].grid()
        self.reconnect_attempt_labels[i].grid()
        self.parent.after(0, lambda: self.action_buttons[i].config(image=self.disconnect_image))
        self.parent.after(0, lambda: self.entries[i].config(state="disabled"))
        self.update_circle_state([False, False, True, False], i)
        self.show_bar(i, True)
        self.virtual_keyboard.hide()
        self.blink_pwr(i)
        self.save_ip_settings()
        self.entries[i].event_generate("<FocusOut>")

    def disconnect(self, i, manual=False):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            threading.Thread(target=self.disconnect_client,
                             args=(ip, i, manual), daemon=True).start()

    def disconnect_client(self, ip, i, manual=False):
        self.stop_flags[ip].set()
        self.connected_clients[ip].join(timeout=5)
        self.clients[ip].close()
        del self.connected_clients[ip]
        del self.clients[ip]
        del self.stop_flags[ip]

        self.parent.after(0, lambda: self.reset_ui_elements(i))
        self.parent.after(0, lambda: self.action_buttons[i].config(image=self.connect_image))
        self.parent.after(0, lambda: self.entries[i].config(state="normal"))
        self.parent.after(0, lambda: self.box_frames[i].config(highlightthickness=1))
        self.save_ip_settings()

        if manual:
            self.box_frames[i].after(0, lambda: self.box_states[i]["gms1000_text_id"] and self.box_states[i]["gms1000_text_id"])
            self.disconnection_labels[i].grid_remove()
            self.reconnect_attempt_labels[i].grid_remove()

    def connect_to_server(self, ip, client):
        for attempt in range(5):
            if client.connect():
                self.console.print(f"Connected to {ip}")
                return True
            self.console.print(f"Attempt {attempt+1} failed, retrying...")
            time.sleep(2)
        return False

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        start_addr = BASE - 1
        count = 11
        while not stop_flag.is_set():
            try:
                if not client.is_socket_open():
                    raise ConnectionException("Socket closed")
                resp = client.read_holding_registers(start_addr, count)
                if resp.isError():
                    raise ModbusIOException("Read error")
                regs = resp.registers
                # 알람
                bit6 = bool(regs[0] & (1<<6))
                bit7 = bool(regs[0] & (1<<7))
                self.box_states[box_index]["alarm1_on"] = bit6
                self.box_states[box_index]["alarm2_on"] = bit7
                self.ui_update_queue.put(('alarm_check', box_index))
                # 세그먼트
                bits = [bool(regs[7] & (1<<n)) for n in range(4)]
                if not any(bits):
                    val = str(regs[4])
                    self.data_queue.put((box_index, val, False))
                else:
                    disp = ""
                    for idx,flag in enumerate(bits):
                        if flag:
                            disp = BIT_TO_SEGMENT[idx]
                            break
                    disp = disp.ljust(4)
                    blink = 'E' in disp
                    self.box_states[box_index]["blinking_error"] = blink
                    self.data_queue.put((box_index, disp, blink))
                    self.ui_update_queue.put(
                        ('circle_state', box_index, [False, False, True, blink])
                    )
                # 바
                self.ui_update_queue.put(('bar', box_index, regs[10]))
                time.sleep(self.communication_interval)
            except (ConnectionException, ModbusIOException) as e:
                self.console.print(f"Connection lost: {e}")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break
            except Exception as e:
                self.console.print(f"Error: {e}")
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break

    def update_circle_state(self, states, box_index):
        canvas, circles, *_ = self.box_data[box_index]
        colors_on = ['red','red','green','yellow']
        colors_off = ['#fdc8c8','#fdc8c8','#e0fbba','#fcf1bf']
        for i, st in enumerate(states):
            col = colors_on[i] if st else colors_off[i]
            canvas.itemconfig(circles[i], fill=col, outline=col)
        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"modbus_{box_index}")

    def update_segment_display(self, value, box_index, blink):
        canvas, _, _, _, _ = self.box_data[box_index]
        val = value.zfill(4)
        prev = self.box_states[box_index]["previous_segment_display"]
        if val != prev:
            self.box_states[box_index]["previous_segment_display"] = val
        leading = True
        for idx, ch in enumerate(val):
            segs = SEGMENTS[' '] if leading and ch=='0' and idx<3 else SEGMENTS.get(ch, SEGMENTS[' '])
            if segs != SEGMENTS[' ']:
                leading = False
            if blink and self.box_states[box_index]["blink_state"]:
                segs = SEGMENTS[' ']
            for j, bit in enumerate(segs):
                tag = f'segment_{idx}_{chr(97+j)}'
                color = '#fc0c0c' if bit=='1' else '#424242'
                if canvas.find_withtag(tag):
                    canvas.itemconfig(tag, fill=color)
        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def update_bar(self, value, box_index):
        _, _, bar_cv, grad_img, bar_item = self.box_data[box_index]
        perc = value / 100.0
        length = int(153 * SCALE_FACTOR * perc)
        img = self.gradient_bar.crop((0,0,length,int(5*SCALE_FACTOR)))
        tkimg = ImageTk.PhotoImage(img)
        bar_cv.itemconfig(bar_item, image=tkimg)
        bar_cv.bar_image = tkimg

    def show_bar(self, box_index, show):
        _, _, bar_cv, _, bar_item = self.box_data[box_index]
        state = 'normal' if show else 'hidden'
        bar_cv.itemconfig(bar_item, state=state)

    def handle_disconnection(self, box_index):
        self.disconnection_counts[box_index] += 1
        self.disconnection_labels[box_index].config(
            text=f"DC: {self.disconnection_counts[box_index]}"
        )
        self.ui_update_queue.put(('circle_state', box_index, [False]*4))
        self.ui_update_queue.put(('segment_display', box_index, "    ", False))
        self.ui_update_queue.put(('bar', box_index, 0))
        self.parent.after(0, lambda idx=box_index: self.action_buttons[idx].config(image=self.connect_image))
        self.parent.after(0, lambda idx=box_index: self.entries[idx].config(state="normal"))
        self.parent.after(0, lambda idx=box_index: self.box_frames[idx].config(highlightthickness=1))
        self.box_states[box_index]["pwr_blinking"] = False
        self.box_states[box_index]["pwr_blink_state"] = False

    def reconnect(self, ip, client, stop_flag, box_index):
        retries = 0
        while retries < 5 and not stop_flag.is_set():
            time.sleep(2)
            self.console.print(f"Reconnecting {ip} ({retries+1}/5)")
            self.parent.after(0, lambda idx=box_index, r=retries:
                self.reconnect_attempt_labels[idx].config(text=f"Reconnect: {r+1}/5")
            )
            if client.connect():
                stop_flag.clear()
                threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, box_index),
                    daemon=True
                ).start()
                self.parent.after(0, lambda idx=box_index:
                    self.action_buttons[idx].config(image=self.disconnect_image)
                )
                self.parent.after(0, lambda idx=box_index:
                    self.entries[idx].config(state="disabled")
                )
                self.parent.after(0, lambda idx=box_index:
                    self.box_frames[idx].config(highlightthickness=0)
                )
                self.ui_update_queue.put(('circle_state', box_index, [False,False,True,False]))
                self.blink_pwr(box_index)
                self.show_bar(box_index, True)
                self.parent.after(0, lambda idx=box_index:
                    self.reconnect_attempt_labels[idx].config(text="Reconnect: OK")
                )
                return
            retries += 1
        self.auto_reconnect_failed[box_index] = True
        self.parent.after(0, lambda idx=box_index:
            self.reconnect_attempt_labels[idx].config(text="Reconnect: Failed")
        )
        self.disconnect_client(ip, box_index, manual=False)

    def blink_pwr(self, box_index):
        if self.box_states[box_index]["pwr_blinking"]:
            return
        self.box_states[box_index]["pwr_blinking"] = True
        def toggle():
            if not self.box_states[box_index]["pwr_blinking"]:
                return
            canvas, circles, *_ = self.box_data[box_index]
            if self.ip_vars[box_index].get() not in self.connected_clients:
                canvas.itemconfig(circles[2], fill="#e0fbba", outline="#e0fbba")
                self.box_states[box_index]["pwr_blink_state"] = False
                self.box_states[box_index]["pwr_blinking"] = False
                return
            state = self.box_states[box_index]["pwr_blink_state"]
            color = "red" if state else "green"
            canvas.itemconfig(circles[2], fill=color, outline=color)
            self.box_states[box_index]["pwr_blink_state"] = not state
            if self.ip_vars[box_index].get() in self.connected_clients:
                self.parent.after(self.blink_interval, toggle)
        toggle()

    def check_alarms(self, box_index):
        a1 = self.box_states[box_index]["alarm1_on"]
        a2 = self.box_states[box_index]["alarm2_on"]
        if a2:
            self.box_states[box_index]["alarm1_blinking"] = False
            self.box_states[box_index]["alarm2_blinking"] = True
            self.box_states[box_index]["alarm_border_blink"] = True
            self.set_alarm_lamp(box_index, True, False, True, True)
            self.blink_alarms(box_index)
        elif a1:
            self.box_states[box_index]["alarm1_blinking"] = True
            self.box_states[box_index]["alarm2_blinking"] = False
            self.box_states[box_index]["alarm_border_blink"] = True
            self.set_alarm_lamp(box_index, True, True, False, False)
            self.blink_alarms(box_index)
        else:
            self.box_states[box_index].update({
                "alarm1_blinking":False, "alarm2_blinking":False,
                "alarm_border_blink":False, "border_blink_state":False
            })
            self.set_alarm_lamp(box_index, False, False, False, False)
            canvas = self.box_data[box_index][0]
            canvas.config(highlightbackground="#000")

    def set_alarm_lamp(self, box_index, a1_on, b1, a2_on, b2):
        canvas, circles, *_ = self.box_data[box_index]
        # AL1
        col1 = "red" if (a1_on and not b1) or (not a1_on and False) else "#fdc8c8"
        canvas.itemconfig(circles[0], fill=col1, outline=col1)
        # AL2
        col2 = "red" if (a2_on and not b2) else "#fdc8c8"
        canvas.itemconfig(circles[1], fill=col2, outline=col2)

    def blink_alarms(self, box_index):
        if not (self.box_states[box_index]["alarm1_blinking"] or
                self.box_states[box_index]["alarm2_blinking"] or
                self.box_states[box_index]["alarm_border_blink"]):
            return
        canvas, circles, *_ = self.box_data[box_index]
        state = self.box_states[box_index]["border_blink_state"]
        self.box_states[box_index]["border_blink_state"] = not state
        # 테두리
        color = "#ff0000" if not state else "#000"
        canvas.config(highlightbackground=color)
        # AL1 깜박
        if self.box_states[box_index]["alarm1_blinking"]:
            cur = canvas.itemcget(circles[0],"fill")
            canvas.itemconfig(circles[0],
                              fill="#fdc8c8" if cur=="red" else "red",
                              outline="#fdc8c8" if cur=="red" else "red")
        # AL2 깜박
        if self.box_states[box_index]["alarm2_blinking"]:
            cur = canvas.itemcget(circles[1],"fill")
            canvas.itemconfig(circles[1],
                              fill="#fdc8c8" if cur=="red" else "red",
                              outline="#fdc8c8" if cur=="red" else "red")
        self.parent.after(self.alarm_blink_interval,
                          lambda idx=box_index: self.blink_alarms(idx))

    def reset_ui_elements(self, box_index):
        self.update_circle_state([False]*4, box_index)
        self.update_segment_display("    ", box_index, False)
        self.show_bar(box_index, False)
        self.console.print(f"Reset UI for box {box_index}")

    def check_click(self, event):
        # 클릭 이벤트 처리 필요시 구현
        pass

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

    ui = ModbusUI(root, num_boxes, gas_types, alarm_callback)

    row = col = 0
    max_col = 2
    for frame in ui.box_frames:
        frame.grid(row=row, column=col, padx=10, pady=10)
        col += 1
        if col >= max_col:
            col = 0
            row += 1

    root.mainloop()

if __name__ == "__main__":
    main()
