import json
import os
import time
import shutil
import threading
import queue
import socket
from tkinter import (
    Frame,
    Canvas,
    StringVar,
    Entry,
    Button,
    Tk,
    Label,
    filedialog,
    messagebox,
    Toplevel,
    Text,
    Scrollbar,
)
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse
from rich.console import Console
from PIL import Image, ImageTk
from common import SEGMENTS, BIT_TO_SEGMENT, create_segment_display, create_gradient_bar
from virtual_keyboard import VirtualKeyboard


def get_local_ip() -> str:
    """
    라즈베리파이(해당 장비)의 IP를 구해서 반환.
    실패하면 127.0.0.1로 fallback.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # 실제 연결은 안 되고 라우팅 정보만 사용
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


SCALE_FACTOR = 1.65
# 기본 TFTP IP를 장비(라즈베리파이)의 IP로 설정
DEFAULT_TFTP_IP = get_local_ip()
TFTP_FW_BASENAME = 'ASGD3200E.bin'   # 필요시 참고용 이름
TFTP_ROOT_DIR = '/srv/tftp'

# 장비가 TFTP로 실제 요청하는 경로: GDS/ASGD-3200/asgd3200.bin
TFTP_DEVICE_SUBDIR = os.path.join('GDS', 'ASGD-3200')  # -> "GDS/ASGD-3200"
TFTP_DEVICE_FILENAME = 'asgd3200.bin'                  # RRQ "GDS/ASGD-3200/asgd3200.bin"


def sx(x: float) -> int:
    return int(x * SCALE_FACTOR)


def sy(y: float) -> int:
    return int(y * SCALE_FACTOR)


def encode_ip_to_words(ip: str):
    try:
        a, b, c, d = map(int, ip.split('.'))
    except ValueError:
        raise ValueError(f'Invalid IP format: {ip}')
    for octet in (a, b, c, d):
        if not 0 <= octet <= 255:
            raise ValueError(f'Invalid octet in IP: {ip}')
    word1 = (a << 8) | b
    word2 = (c << 8) | d
    return (word1, word2)


class ModbusUI:
    SETTINGS_FILE = 'modbus_settings.json'
    GAS_FULL_SCALE = {'ORG': 9999, 'ARF-T': 5000, 'HMDS': 3000, 'HC-100': 5000}
    GAS_TYPE_POSITIONS = {
        'ORG': (sx(115), sy(100)),
        'ARF-T': (sx(107), sy(100)),
        'HMDS': (sx(110), sy(100)),
        'HC-100': (sx(104), sy(100)),
    }
    LAMP_COLORS_ON = ['red', 'red', 'green', 'yellow']
    LAMP_COLORS_OFF = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']

    LOG_MAX_ENTRIES = 1000  # 박스별 최대 로그 라인 수

    @staticmethod
    def reg_addr(addr_4xxxx: int) -> int:
        return addr_4xxxx - 40001

    def __init__(self, parent, num_boxes, gas_types, alarm_callback):
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.virtual_keyboard = VirtualKeyboard(parent)

        self.ip_vars = [StringVar() for _ in range(num_boxes)]
        # TFTP IP는 화면에 안 보이지만 내부적으로 사용
        self.tftp_ip_vars = [StringVar(value=DEFAULT_TFTP_IP) for _ in range(num_boxes)]
        self.fw_file_paths = [None for _ in range(num_boxes)]

        self.entries = []
        self.action_buttons = []
        self.clients = {}
        self.connected_clients = {}
        self.stop_flags = {}
        self.modbus_locks = {}
        self.data_queue = queue.Queue()
        self.ui_update_queue = queue.Queue()
        self.console = Console()
        self.box_states = []
        self.box_frames = []
        self.box_data = []
        self.gradient_bar = create_gradient_bar(sx(120), sy(5))
        self.gas_types = gas_types

        self.disconnection_counts = [0] * num_boxes
        self.disconnection_labels = [None] * num_boxes
        self.auto_reconnect_failed = [False] * num_boxes
        self.reconnect_attempt_labels = [None] * num_boxes

        # FW 상태 로그 중복 방지용
        self.last_fw_status = [None] * num_boxes

        # 설정 팝업 (FW / ZERO / RST / TFTP) - 램프에서 열리는 팝업
        self.settings_popups = [None] * num_boxes

        # 세그먼트 로그 뷰어 팝업
        self.log_popups = [None] * num_boxes
        self.log_popup_texts = [None] * num_boxes
        # 박스별 로그 버퍼
        self.box_logs = [[] for _ in range(num_boxes)]

        self.load_ip_settings(num_boxes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        connect_image_path = os.path.join(script_dir, 'img/on.png')
        disconnect_image_path = os.path.join(script_dir, 'img/off.png')
        self.connect_image = self.load_image(connect_image_path, (sx(50), sy(70)))
        self.disconnect_image = self.load_image(disconnect_image_path, (sx(50), sy(70)))

        for i in range(num_boxes):
            self.create_modbus_box(i)

        self.communication_interval = 0.2
        self.blink_interval = int(self.communication_interval * 1000)
        self.alarm_blink_interval = 1000  # 알람 깜빡이 주기(1초)

        self.start_data_processing_thread()
        self.schedule_ui_update()

    def load_ip_settings(self, num_boxes):
        if os.path.exists(self.SETTINGS_FILE):
            with open(self.SETTINGS_FILE, 'r') as file:
                ip_settings = json.load(file)
                for i in range(min(num_boxes, len(ip_settings))):
                    self.ip_vars[i].set(ip_settings[i])

    def save_ip_settings(self):
        ip_settings = [ip_var.get() for ip_var in self.ip_vars]
        with open(self.SETTINGS_FILE, 'w') as file:
            json.dump(ip_settings, file)

    def load_image(self, path, size):
        img = Image.open(path).convert('RGBA')
        img.thumbnail(size, Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def add_ip_row(self, frame, ip_var, index):
        entry_border = Frame(frame, bg='#4a4a4a', bd=1, relief='solid')
        entry_border.grid(row=0, column=0, padx=(0, 0), pady=5)
        entry = Entry(
            entry_border,
            textvariable=ip_var,
            width=int(7 * SCALE_FACTOR),
            highlightthickness=0,
            bd=0,
            relief='flat',
            bg='#2e2e2e',
            fg='white',
            insertbackground='white',
            font=('Helvetica', int(10 * SCALE_FACTOR)),
            justify='center',
        )
        entry.pack(padx=2, pady=3)
        placeholder_text = f'{index + 1}. IP를 입력해주세요.'
        if not ip_var.get():
            entry.insert(0, placeholder_text)
            entry.config(fg='#a9a9a9')
        else:
            entry.config(fg='white')

        def on_focus_in(event, e=entry, p=placeholder_text):
            if e['state'] == 'normal':
                if e.get() == p:
                    e.delete(0, 'end')
                    e.config(fg='white')
                entry_border.config(bg='#1e90ff')
                e.config(bg='#3a3a3a')

        def on_focus_out(event, e=entry, p=placeholder_text):
            if e['state'] == 'normal':
                if not e.get():
                    e.insert(0, p)
                    e.config(fg='#a9a9a9')
                entry_border.config(bg='#4a4a4a')
                e.config(bg='#2e2e2e')

        def on_entry_click(event, e=entry, p=placeholder_text):
            if e['state'] == 'normal':
                on_focus_in(event, e, p)
                self.show_virtual_keyboard(e)

        entry.bind('<FocusIn>', on_focus_in)
        entry.bind('<FocusOut>', on_focus_out)
        entry.bind('<Button-1>', on_entry_click)

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
            cursor='hand2',
        )
        action_button.grid(row=0, column=1)
        self.action_buttons.append(action_button)
        self.entries.append(entry)

    def show_virtual_keyboard(self, entry):
        self.virtual_keyboard.show(entry)
        entry.focus_set()

    def create_modbus_box(self, index):
        # 🔴 알람 테두리용: box_frame의 highlight를 사용
        box_frame = Frame(
            self.parent,
            highlightthickness=1,
            highlightbackground='#000000',
            highlightcolor='#000000',
        )
        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(
            inner_frame,
            width=sx(150),
            height=sy(300),
            highlightthickness=sx(3),
            highlightbackground='#000000',
            highlightcolor='#000000',
            bg='#1e1e1e',
        )
        box_canvas.pack()
        box_canvas.create_rectangle(0, 0, sx(160), sy(200), fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, sy(200), sx(260), sy(310), fill='black', outline='grey', tags='border')

        # 7-Segment 표시 생성 (create_segment_display 안에서 box_canvas.segment_canvas 생성 가능)
        create_segment_display(box_canvas)

        # 세그먼트(검은 숫자창) 클릭 영역 - 투명 Rect
        seg_x1, seg_y1 = sx(10), sy(25)
        seg_x2, seg_y2 = sx(150 - 10), sy(90)
        box_canvas.create_rectangle(
            seg_x1, seg_y1, seg_x2, seg_y2,
            outline='',
            fill='',
            tags='segment_click_area'
        )

        gas_key = self.gas_types.get(f'modbus_box_{index}', 'ORG')
        gas_type_var = StringVar(value=gas_key)
        fw_name_var = StringVar(value='(파일 없음)')

        self.box_states.append(
            {
                'blink_state': False,
                'blinking_error': False,
                'previous_value_40011': None,
                'previous_segment_display': None,
                'pwr_blink_state': False,
                'pwr_blinking': False,
                'gas_type_var': gas_type_var,
                'gas_type_text_id': None,
                'full_scale': self.GAS_FULL_SCALE[gas_key],
                'alarm1_on': False,
                'alarm2_on': False,
                'alarm1_blinking': False,
                'alarm2_blinking': False,
                'alarm_border_blink': False,
                'border_blink_state': False,
                'gms1000_text_id': None,
                'fw_file_name_var': fw_name_var,
                'fw_upgrading': False,   # FW 업그레이드 진행 중인지 여부
                'alarm_blink_running': False,  # 알람 깜빡이 루프 동작 여부
                'segment_click_area': (seg_x1, seg_y1, seg_x2, seg_y2),
                # 로그 비교용 이전 상태
                'last_log_value': None,
                'last_log_alarm1': None,
                'last_log_alarm2': None,
                'last_log_error_reg': None,
            }
        )

        # 세그먼트 클릭 → 로그 팝업
        def _on_segment_click(event, idx=index):
            self.open_segment_popup(idx)

        # 1) 같은 캔버스의 투명 영역
        box_canvas.tag_bind('segment_click_area', '<Button-1>', _on_segment_click)
        # 2) create_segment_display에서 만든 segment_canvas가 위에 떠 있으면, 거기에 직접 바인딩
        if hasattr(box_canvas, 'segment_canvas'):
            box_canvas.segment_canvas.bind('<Button-1>', _on_segment_click)

        control_frame = Frame(box_canvas, bg='black')
        control_frame.place(x=sx(10), y=sy(210))

        ip_var = self.ip_vars[index]
        self.add_ip_row(control_frame, ip_var, index)

        disconnection_label = Label(
            control_frame,
            text=f'DC: {self.disconnection_counts[index]}',
            fg='white',
            bg='black',
            font=('Helvetica', int(10 * SCALE_FACTOR)),
        )
        disconnection_label.grid(row=1, column=0, columnspan=2, pady=(2, 0))
        self.disconnection_labels[index] = disconnection_label

        reconnect_label = Label(
            control_frame,
            text='Reconnect: 0/5',
            fg='yellow',
            bg='black',
            font=('Helvetica', int(10 * SCALE_FACTOR)),
        )
        reconnect_label.grid(row=2, column=0, columnspan=2, pady=(2, 0))
        self.reconnect_attempt_labels[index] = reconnect_label

        disconnection_label.grid_remove()
        reconnect_label.grid_remove()

        circle_al1 = box_canvas.create_oval(sx(77) - sx(20), sy(200) - sy(32), sx(87) - sx(20), sy(190) - sy(32))
        box_canvas.create_text(
            sx(95) - sx(25),
            sy(222) - sy(40),
            text='AL1',
            fill='#cccccc',
            anchor='e',
        )
        circle_al2 = box_canvas.create_oval(sx(133) - sy(30), sy(200) - sy(32), sx(123) - sy(30), sy(190) - sy(32))
        box_canvas.create_text(
            sx(140) - sy(35),
            sy(222) - sy(40),
            text='AL2',
            fill='#cccccc',
            anchor='e',
        )
        circle_pwr = box_canvas.create_oval(sx(30) - sx(10), sy(200) - sy(32), sx(40) - sx(10), sy(190) - sy(32))
        box_canvas.create_text(
            sx(35) - sx(10),
            sy(222) - sy(40),
            text='PWR',
            fill='#cccccc',
            anchor='center',
        )
        circle_fut = box_canvas.create_oval(sx(171) - sy(40), sy(200) - sy(32), sx(181) - sy(40), sy(190) - sy(32))
        box_canvas.create_text(
            sx(175) - sy(40),
            sy(217) - sy(40),
            text='FUT',
            fill='#cccccc',
            anchor='n',
        )

        # 램프 클릭 → 설정 팝업(기존 기능 유지)
        def _on_lamp_click(event, idx=index):
            self.open_settings_popup(idx)

        box_canvas.tag_bind(circle_pwr, "<Button-1>", _on_lamp_click)
        box_canvas.tag_bind(circle_al1, "<Button-1>", _on_lamp_click)
        box_canvas.tag_bind(circle_al2, "<Button-1>", _on_lamp_click)
        box_canvas.tag_bind(circle_fut, "<Button-1>", _on_lamp_click)

        gas_pos = self.GAS_TYPE_POSITIONS[gas_type_var.get()]
        gas_type_text_id = box_canvas.create_text(
            *gas_pos,
            text=gas_type_var.get(),
            font=('Helvetica', int(16 * SCALE_FACTOR), 'bold'),
            fill='#cccccc',
            anchor='center',
        )
        self.box_states[index]['gas_type_text_id'] = gas_type_text_id

        gms1000_text_id = box_canvas.create_text(
            sx(80),
            sy(270),
            text='GMS-1000',
            font=('Helvetica', int(16 * SCALE_FACTOR), 'bold'),
            fill='#cccccc',
            anchor='center',
        )
        self.box_states[index]['gms1000_text_id'] = gms1000_text_id

        box_canvas.create_text(
            sx(80),
            sy(295),
            text='GDS ENGINEERING CO.,LTD',
            font=('Helvetica', int(7 * SCALE_FACTOR), 'bold'),
            fill='#cccccc',
            anchor='center',
        )

        bar_canvas = Canvas(box_canvas, width=sx(120), height=sy(5), bg='white', highlightthickness=0)
        bar_canvas.place(x=sx(18.5), y=sy(75))
        bar_image = ImageTk.PhotoImage(self.gradient_bar)
        bar_item = bar_canvas.create_image(0, 0, anchor='nw', image=bar_image)

        self.box_frames.append(box_frame)
        self.box_data.append((box_canvas, [circle_al1, circle_al2, circle_pwr, circle_fut], bar_canvas, bar_image, bar_item))

        self.show_bar(index, show=False)
        self.update_circle_state([False, False, False, False], box_index=index)

    def select_fw_file(self, box_index: int):
        file_path = filedialog.askopenfilename(
            title='FW 파일 선택', filetypes=[('BIN files', '*.bin'), ('All files', '*.*')]
        )
        if not file_path:
            return
        self.fw_file_paths[box_index] = file_path
        basename = os.path.basename(file_path)
        self.box_states[box_index]['fw_file_name_var'].set(basename)
        self.console.print(f'[FW] box {box_index} using file: {file_path}')

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]['full_scale'] = full_scale
        box_canvas = self.box_data[box_index][0]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]['gas_type_text_id'], *position)
        box_canvas.itemconfig(self.box_states[box_index]['gas_type_text_id'], text=gas_type)

    def update_circle_state(self, states, box_index=0):
        box_canvas, circle_items, _, _, _ = self.box_data[box_index]
        for i, state in enumerate(states):
            color = self.LAMP_COLORS_ON[i] if state else self.LAMP_COLORS_OFF[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)
        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f'modbus_{box_index}')

    def update_segment_display(self, value, box_index=0, blink=False):
        box_canvas = self.box_data[box_index][0]
        value = value.zfill(4)
        prev_val = self.box_states[box_index]['previous_segment_display']
        if value != prev_val:
            self.box_states[box_index]['previous_segment_display'] = value
        leading_zero = True
        for idx, digit in enumerate(value):
            if leading_zero and digit == '0' and idx < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS.get(digit, SEGMENTS[' '])
                leading_zero = False
            if blink and self.box_states[box_index]['blink_state']:
                segments = SEGMENTS[' ']
            for j, seg_on in enumerate(segments):
                color = '#fc0c0c' if seg_on == '1' else '#424242'
                segment_tag = f'segment_{idx}_{chr(97 + j)}'
                if hasattr(box_canvas, 'segment_canvas') and box_canvas.segment_canvas.find_withtag(segment_tag):
                    box_canvas.segment_canvas.itemconfig(segment_tag, fill=color)
        self.box_states[box_index]['blink_state'] = not self.box_states[box_index]['blink_state']

    def update_bar(self, value, box_index):
        _, _, bar_canvas, _, bar_item = self.box_data[box_index]
        percentage = value / 100.0
        if percentage < 0:
            percentage = 0
        if percentage > 1:
            percentage = 1
        bar_length = int(153 * SCALE_FACTOR * percentage)
        cropped_image = self.gradient_bar.crop((0, 0, bar_length, sy(5)))
        bar_image = ImageTk.PhotoImage(cropped_image)
        bar_canvas.itemconfig(bar_item, image=bar_image)
        bar_canvas.bar_image = bar_image

    def show_bar(self, box_index, show):
        bar_canvas = self.box_data[box_index][2]
        bar_item = self.box_data[box_index][4]
        bar_canvas.itemconfig(bar_item, state='normal' if show else 'hidden')

    def toggle_connection(self, i):
        if self.ip_vars[i].get() in self.connected_clients:
            self.disconnect(i, manual=True)
        else:
            threading.Thread(target=self.connect, args=(i,), daemon=True).start()

    def connect(self, i):
        ip = self.ip_vars[i].get()
        if self.auto_reconnect_failed[i]:
            self.disconnection_counts[i] = 0
            self.disconnection_labels[i].config(text='DC: 0')
            self.auto_reconnect_failed[i] = False

        if ip and ip not in self.connected_clients:
            client = ModbusTcpClient(ip, port=502, timeout=3)
            if self.connect_to_server(ip, client):
                stop_flag = threading.Event()
                self.stop_flags[ip] = stop_flag
                self.clients[ip] = client
                self.modbus_locks[ip] = threading.Lock()
                t = threading.Thread(
                    target=self.read_modbus_data,
                    args=(ip, client, stop_flag, i),
                    daemon=True,
                )
                self.connected_clients[ip] = t
                t.start()
                self.console.print(f'Started data thread for {ip}')

                # 장비의 TFTP IP는 약간 딜레이를 두고 백그라운드에서 읽기
                threading.Thread(
                    target=self.delayed_load_tftp_ip_from_device,
                    args=(i, 1.0),
                    daemon=True,
                ).start()

                box_canvas = self.box_data[i][0]
                gms1000_id = self.box_states[i]['gms1000_text_id']
                box_canvas.itemconfig(gms1000_id, state='hidden')

                self.disconnection_labels[i].grid()
                self.reconnect_attempt_labels[i].grid()

                self.parent.after(
                    0,
                    lambda idx=i: self.action_buttons[idx].config(
                        image=self.disconnect_image,
                        relief='flat',
                        borderwidth=0,
                    ),
                )
                self.parent.after(0, lambda idx=i: self.entries[idx].config(state='disabled'))

                self.update_circle_state([False, False, True, False], box_index=i)
                self.show_bar(i, show=True)
                self.virtual_keyboard.hide()
                self.blink_pwr(i)
                self.save_ip_settings()
                self.entries[i].event_generate('<FocusOut>')
            else:
                self.console.print(f'Failed to connect to {ip}')
                self.parent.after(
                    0,
                    lambda idx=i: self.update_circle_state(
                        [False, False, False, False], box_index=idx
                    ),
                )

    def disconnect(self, i, manual=False):
        ip = self.ip_vars[i].get()
        if ip in self.connected_clients:
            threading.Thread(
                target=self.disconnect_client,
                args=(ip, i, manual),
                daemon=True,
            ).start()

    def disconnect_client(self, ip, i, manual=False):
        stop_flag = self.stop_flags.get(ip)
        if stop_flag is not None:
            stop_flag.set()

        t = self.connected_clients.get(ip)
        current = threading.current_thread()
        if t is not None and t is not current:
            t.join(timeout=5)
            if t.is_alive():
                self.console.print(f'Thread for {ip} did not terminate in time.')
        elif t is not None:
            self.console.print(f'Skipping join on current thread for {ip}')

        client = self.clients.get(ip)
        if client is not None:
            client.close()
        self.console.print(f'Disconnected from {ip}')

        self.cleanup_client(ip)
        self.parent.after(0, lambda idx=i, m=manual: self._after_disconnect(idx, m))
        self.save_ip_settings()

    def _after_disconnect(self, i, manual):
        self.reset_ui_elements(i)
        self.action_buttons[i].config(
            image=self.connect_image,
            relief='flat',
            borderwidth=0,
        )
        self.entries[i].config(state='normal')
        self.box_frames[i].config(highlightthickness=1, highlightbackground='#000000')
        if manual:
            box_canvas = self.box_data[i][0]
            gms1000_id = self.box_states[i]['gms1000_text_id']
            box_canvas.itemconfig(gms1000_id, state='normal')
            self.disconnection_labels[i].grid_remove()
            self.reconnect_attempt_labels[i].grid_remove()

    def reset_ui_elements(self, box_index):
        self.update_circle_state([False, False, False, False], box_index=box_index)
        self.update_segment_display('    ', box_index=box_index)
        self.show_bar(box_index, show=False)
        self.console.print(f'Reset UI elements for box {box_index}')

    def cleanup_client(self, ip):
        self.connected_clients.pop(ip, None)
        self.clients.pop(ip, None)
        self.stop_flags.pop(ip, None)
        self.modbus_locks.pop(ip, None)

    def connect_to_server(self, ip, client):
        retries = 5
        for attempt in range(retries):
            if client.connect():
                self.console.print(f'Connected to the Modbus server at {ip}')
                return True
            self.console.print(
                f'Connection attempt {attempt + 1} to {ip} failed. Retrying in 2 seconds...'
            )
            time.sleep(2)
        return False

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        start_address = self.reg_addr(40001)
        num_registers = 24

        while not stop_flag.is_set():
            try:
                if client is None or not client.is_socket_open():
                    raise ConnectionException('Socket is closed')

                lock = self.modbus_locks.get(ip)
                if lock is None:
                    break

                with lock:
                    response = client.read_holding_registers(start_address, num_registers)

                if response.isError():
                    raise ModbusIOException(
                        f'Error reading from {ip}, address 40001~40024'
                    )

                raw_regs = response.registers
                value_40001 = raw_regs[0]
                value_40005 = raw_regs[4]
                # 에러코드/에러 비트 레지스터: 40008 → raw_regs[7]
                value_40007 = raw_regs[7]
                value_40011 = raw_regs[10]
                value_40022 = raw_regs[21]
                value_40023 = raw_regs[22]
                value_40024 = raw_regs[23]

                bit_6_on = bool(value_40001 & (1 << 6))
                bit_7_on = bool(value_40001 & (1 << 7))
                self.box_states[box_index]['alarm1_on'] = bit_6_on
                self.box_states[box_index]['alarm2_on'] = bit_7_on
                self.ui_update_queue.put(('alarm_check', box_index))

                # 값 / AL1 / AL2 / 에러레지스터 변화만 로그로 기록
                self.maybe_log_event(
                    box_index,
                    value_40005,
                    bit_6_on,
                    bit_7_on,
                    value_40007,
                )

                bits = [bool(value_40007 & (1 << n)) for n in range(4)]
                if not any(bits):
                    formatted_value = f'{value_40005}'
                    self.data_queue.put((box_index, formatted_value, False))
                else:
                    error_display = ''
                    for bit_index, bit_flag in enumerate(bits):
                        if bit_flag:
                            error_display = BIT_TO_SEGMENT[bit_index]
                            break
                    error_display = error_display.ljust(4)

                    if 'E' in error_display:
                        self.box_states[box_index]['blinking_error'] = True
                        self.data_queue.put((box_index, error_display, True))
                        self.ui_update_queue.put(
                            (
                                'circle_state',
                                box_index,
                                [False, False, True, self.box_states[box_index]['blink_state']],
                            )
                        )
                    else:
                        self.box_states[box_index]['blinking_error'] = False
                        self.data_queue.put((box_index, error_display, False))
                        self.ui_update_queue.put(
                            ('circle_state', box_index, [False, False, True, False])
                        )

                self.ui_update_queue.put(('bar', box_index, value_40011))
                self.ui_update_queue.put(
                    ('fw_status', box_index, value_40022, value_40023, value_40024)
                )

                time.sleep(self.communication_interval)

            except ConnectionException as e:
                self.console.print(f'Connection to {ip} lost: {e}')
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break
            except ModbusIOException as e:
                self.console.print(
                    f'Temporary Modbus I/O error from {ip}: {e}. Will retry...'
                )
                time.sleep(self.communication_interval * 2)
                continue
            except Exception as e:
                self.console.print(f'Unexpected error reading data from {ip}: {e}')
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break

    # 로그 기록 로직
    def maybe_log_event(self, box_index, value_40005, alarm1, alarm2, error_reg):
        """
        숫자 값 / AL1 / AL2 / 에러레지스터 중 하나라도 변하면 로그 1줄 추가.
        박스별 최대 LOG_MAX_ENTRIES까지만 유지.
        """
        state = self.box_states[box_index]
        last_val = state.get('last_log_value')
        last_a1 = state.get('last_log_alarm1')
        last_a2 = state.get('last_log_alarm2')
        last_err = state.get('last_log_error_reg')

        if (
            value_40005 == last_val
            and alarm1 == last_a1
            and alarm2 == last_a2
            and error_reg == last_err
        ):
            return  # 변화 없음

        state['last_log_value'] = value_40005
        state['last_log_alarm1'] = alarm1
        state['last_log_alarm2'] = alarm2
        state['last_log_error_reg'] = error_reg

        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        entry = (ts, value_40005, alarm1, alarm2, error_reg)
        logs = self.box_logs[box_index]
        logs.append(entry)
        if len(logs) > self.LOG_MAX_ENTRIES:
            del logs[0]

    def start_data_processing_thread(self):
        threading.Thread(target=self.process_data, daemon=True).start()

    def process_data(self):
        while True:
            try:
                box_index, value, blink = self.data_queue.get(timeout=1)

                # FW 업그레이드 중이면 센서 값으로 7세그를 덮어쓰지 않는다
                if self.box_states[box_index].get('fw_upgrading'):
                    continue

                self.ui_update_queue.put(('segment_display', box_index, value, blink))
            except queue.Empty:
                continue

    def schedule_ui_update(self):
        self.parent.after(100, self.update_ui_from_queue)

    def update_ui_from_queue(self):
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
            elif typ == 'fw_status':
                _, box_index, v_40022, v_40023, v_40024 = item
                self.update_fw_status(box_index, v_40022, v_40023, v_40024)

        self.schedule_ui_update()

    # 세그먼트 클릭 → 로그 뷰어 팝업
    def open_segment_popup(self, box_index: int):
        # 이미 열려 있으면 앞으로 가져오고, 로그 갱신
        existing = self.log_popups[box_index]
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            self.refresh_log_view(box_index)
            return

        win = Toplevel(self.parent)
        win.title(f'Box {box_index + 1} 로그 뷰어')
        win.configure(bg='#1e1e1e')
        win.resizable(True, True)

        self.log_popups[box_index] = win

        def on_close():
            self.log_popups[box_index] = None
            self.log_popup_texts[box_index] = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        Label(
            win,
            text=f'장치 {box_index + 1} 로그 (IP: {self.ip_vars[box_index].get()})',
            fg='white',
            bg='#1e1e1e',
            font=('Helvetica', 12, 'bold'),
        ).pack(padx=10, pady=(10, 5))

        header = Label(
            win,
            text='시간                 값      AL1  AL2  ERR',
            fg='#aaaaaa',
            bg='#1e1e1e',
            font=('Consolas', 10),
        )
        header.pack(padx=10, pady=(0, 0), anchor='w')

        log_frame = Frame(win, bg='#1e1e1e')
        log_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        scrollbar = Scrollbar(log_frame)
        scrollbar.pack(side='right', fill='y')

        text = Text(
            log_frame,
            bg='#121212',
            fg='#f0f0f0',
            insertbackground='white',
            font=('Consolas', 10),
            yscrollcommand=scrollbar.set,
            wrap='none',
        )
        text.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=text.yview)

        self.log_popup_texts[box_index] = text

        # 버튼들
        btn_frame = Frame(win, bg='#1e1e1e')
        btn_frame.pack(padx=10, pady=(0, 10))

        def clear_log():
            if messagebox.askyesno('로그 삭제', '이 장치의 로그를 모두 삭제할까요?'):
                self.box_logs[box_index].clear()
                self.refresh_log_view(box_index)

        def export_log():
            logs = self.box_logs[box_index]
            if not logs:
                messagebox.showinfo('로그 저장', '저장할 로그가 없습니다.')
                return
            path = filedialog.asksaveasfilename(
                title='로그 파일 저장',
                defaultextension='.txt',
                filetypes=[('Text files', '*.txt'), ('All files', '*.*')],
            )
            if not path:
                return
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('시간,값,AL1,AL2,ERR\n')
                    for ts, val, a1, a2, err_reg in logs:
                        a1_str = 'ON' if a1 else 'OFF'
                        a2_str = 'ON' if a2 else 'OFF'
                        err_str = f'0x{err_reg:04X}'
                        f.write(f'{ts},{val},{a1_str},{a2_str},{err_str}\n')
                messagebox.showinfo('로그 저장', '로그 파일이 저장되었습니다.')
            except Exception as e:
                messagebox.showerror('로그 저장', f'로그 저장 중 오류가 발생했습니다.\n{e}')

        Button(
            btn_frame,
            text='새로고침',
            command=lambda idx=box_index: self.refresh_log_view(idx),
            width=12,
            bg='#555555',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=0, padx=5, pady=5)

        Button(
            btn_frame,
            text='로그 삭제',
            command=clear_log,
            width=12,
            bg='#aa4444',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=1, padx=5, pady=5)

        Button(
            btn_frame,
            text='파일로 저장',
            command=export_log,
            width=12,
            bg='#4444aa',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=2, padx=5, pady=5)

        Button(
            btn_frame,
            text='닫기',
            command=on_close,
            width=10,
            bg='#333333',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=3, padx=5, pady=5)

        # 첫 렌더링
        self.refresh_log_view(box_index)

        # 자동 새로고침 (1초)
        def _auto_refresh():
            if win.winfo_exists() and self.log_popups[box_index] is win:
                self.refresh_log_view(box_index)
                win.after(1000, _auto_refresh)

        win.after(1000, _auto_refresh)

        win.transient(self.parent)
        win.grab_set()
        win.focus_set()

    def refresh_log_view(self, box_index: int):
        text = self.log_popup_texts[box_index]
        if text is None:
            return

        logs = self.box_logs[box_index]
        text.config(state='normal')
        text.delete('1.0', 'end')

        for ts, val, a1, a2, err_reg in logs:
            a1_str = 'ON ' if a1 else 'OFF'
            a2_str = 'ON ' if a2 else 'OFF'
            err_str = f'0x{err_reg:04X}'
            line = f'{ts}  {val:6d}  {a1_str:>3}  {a2_str:>3}  {err_str}\n'
            text.insert('end', line)

        text.see('end')
        text.config(state='disabled')

    def handle_disconnection(self, box_index):
        self.disconnection_counts[box_index] += 1
        count = self.disconnection_counts[box_index]
        self.parent.after(
            0,
            lambda idx=box_index, c=count: self.disconnection_labels[idx].config(
                text=f'DC: {c}'
            ),
        )
        self.ui_update_queue.put(('circle_state', box_index, [False, False, False, False]))
        self.ui_update_queue.put(('segment_display', box_index, '    ', False))
        self.ui_update_queue.put(('bar', box_index, 0))

        self.parent.after(
            0,
            lambda idx=box_index: self.action_buttons[idx].config(
                image=self.connect_image,
                relief='flat',
                borderwidth=0,
            ),
        )
        self.parent.after(
            0, lambda idx=box_index: self.entries[idx].config(state='normal')
        )
        self.parent.after(
            0,
            lambda idx=box_index: self.box_frames[idx].config(
                highlightthickness=1,
                highlightbackground='#000000',
            ),
        )
        self.parent.after(0, lambda idx=box_index: self.reset_ui_elements(idx))

        self.box_states[box_index]['pwr_blink_state'] = False
        self.box_states[box_index]['pwr_blinking'] = False

        def _set_pwr_default(idx=box_index):
            box_canvas = self.box_data[idx][0]
            circle_items = self.box_data[idx][1]
            box_canvas.itemconfig(circle_items[2], fill='#e0fbba', outline='#e0fbba')

        self.parent.after(0, _set_pwr_default)
        self.console.print(
            f'PWR lamp set to default green for box {box_index} due to disconnection.'
        )

    def reconnect(self, ip, client, stop_flag, box_index):
        retries = 0
        max_retries = 5

        while not stop_flag.is_set() and retries < max_retries:
            time.sleep(2)
            self.console.print(
                f'Attempting to reconnect to {ip} (Attempt {retries + 1}/{max_retries})'
            )
            self.parent.after(
                0,
                lambda idx=box_index, r=retries: self.reconnect_attempt_labels[
                    idx
                ].config(text=f'Reconnect: {r + 1}/{max_retries}'),
            )
            try:
                new_client = ModbusTcpClient(ip, port=502, timeout=3)
                if new_client.connect():
                    self.console.print(f'Reconnected to the Modbus server at {ip}')
                    # 이전 client 정리
                    try:
                        if client is not None:
                            client.close()
                    except Exception:
                        pass

                    self.clients[ip] = new_client
                    client = new_client

                    if ip not in self.modbus_locks:
                        self.modbus_locks[ip] = threading.Lock()

                    stop_flag.clear()
                    t = threading.Thread(
                        target=self.read_modbus_data,
                        args=(ip, new_client, stop_flag, box_index),
                        daemon=True,
                    )
                    self.connected_clients[ip] = t
                    t.start()

                    # 재연결 후에도 TFTP IP는 살짝 딜레이 줘서 읽기
                    threading.Thread(
                        target=self.delayed_load_tftp_ip_from_device,
                        args=(box_index, 1.0),
                        daemon=True,
                    ).start()

                    self.parent.after(
                        0,
                        lambda idx=box_index: self.action_buttons[idx].config(
                            image=self.disconnect_image,
                            relief='flat',
                            borderwidth=0,
                        ),
                    )
                    self.parent.after(
                        0,
                        lambda idx=box_index: self.entries[idx].config(
                            state='disabled'
                        ),
                    )
                    self.parent.after(
                        0,
                        lambda idx=box_index: self.box_frames[idx].config(
                            highlightthickness=0
                        ),
                    )

                    self.ui_update_queue.put(
                        ('circle_state', box_index, [False, False, True, False])
                    )
                    self.blink_pwr(box_index)
                    self.show_bar(box_index, show=True)
                    self.parent.after(
                        0,
                        lambda idx=box_index: self.reconnect_attempt_labels[idx].config(
                            text='Reconnect: OK'
                        ),
                    )
                    break

                new_client.close()
                retries += 1
                self.console.print(f'Reconnect attempt to {ip} failed.')
            except Exception as e:
                retries += 1
                self.console.print(f'Reconnect exception for {ip}: {e}')

        if retries >= max_retries:
            self.console.print(
                f'Failed to reconnect to {ip} after {max_retries} attempts.'
            )
            self.auto_reconnect_failed[box_index] = True
            self.parent.after(
                0,
                lambda idx=box_index: self.reconnect_attempt_labels[idx].config(
                    text='Reconnect: Failed'
                ),
            )
            self.disconnect_client(ip, box_index, manual=False)

    def blink_pwr(self, box_index):
        if self.box_states[box_index].get('pwr_blinking', False):
            return
        self.box_states[box_index]['pwr_blinking'] = True

        def toggle_color(idx=box_index):
            state = self.box_states[idx]
            if not state['pwr_blinking']:
                return

            if self.ip_vars[idx].get() not in self.connected_clients:
                box_canvas = self.box_data[idx][0]
                circle_items = self.box_data[idx][1]
                box_canvas.itemconfig(circle_items[2], fill='#e0fbba', outline='#e0fbba')
                state['pwr_blink_state'] = False
                state['pwr_blinking'] = False
                return

            box_canvas = self.box_data[idx][0]
            circle_items = self.box_data[idx][1]
            if state['pwr_blink_state']:
                box_canvas.itemconfig(circle_items[2], fill='red', outline='red')
            else:
                box_canvas.itemconfig(circle_items[2], fill='green', outline='green')
            state['pwr_blink_state'] = not state['pwr_blink_state']

            if self.ip_vars[idx].get() in self.connected_clients:
                self.parent.after(self.blink_interval, toggle_color)

        toggle_color()

    # 알람 처리
    def check_alarms(self, box_index):
        state = self.box_states[box_index]

        alarm1 = state['alarm1_on']
        alarm2 = state['alarm2_on']

        prev_active = (
            state['alarm1_blinking']
            or state['alarm2_blinking']
            or state['alarm_border_blink']
        )

        if alarm2:
            state['alarm1_blinking'] = False
            state['alarm2_blinking'] = True
            state['alarm_border_blink'] = True

        elif alarm1:
            state['alarm1_blinking'] = True
            state['alarm2_blinking'] = False
            state['alarm_border_blink'] = True

        else:
            state['alarm1_blinking'] = False
            state['alarm2_blinking'] = False
            state['alarm_border_blink'] = False
            state['alarm_blink_running'] = False

            self.set_alarm_lamp(
                box_index, alarm1_on=False, blink1=False, alarm2_on=False, blink2=False
            )
            # 🔴 알람 해제 시: 프레임 테두리 초기화
            box_frame = self.box_frames[box_index]
            box_frame.config(
                highlightbackground='#000000',
                highlightthickness=1,
            )
            state['border_blink_state'] = False
            return

        # 🔴 알람이 하나라도 켜져 있으면 프레임 테두리를 두껍게 (깜빡임용)
        self.box_frames[box_index].config(highlightthickness=7)

        if not prev_active and not state['alarm_blink_running']:
            self.blink_alarms(box_index)

    def set_alarm_lamp(self, box_index, alarm1_on, blink1, alarm2_on, blink2):
        box_canvas, circle_items, *_ = self.box_data[box_index]
        if alarm1_on:
            if blink1:
                box_canvas.itemconfig(circle_items[0], fill='#fdc8c8', outline='#fdc8c8')
            else:
                box_canvas.itemconfig(circle_items[0], fill='red', outline='red')
        else:
            box_canvas.itemconfig(circle_items[0], fill='#fdc8c8', outline='#fdc8c8')

        if alarm2_on:
            if blink2:
                box_canvas.itemconfig(circle_items[1], fill='#fdc8c8', outline='#fdc8c8')
            else:
                box_canvas.itemconfig(circle_items[1], fill='red', outline='red')
        else:
            box_canvas.itemconfig(circle_items[1], fill='#fdc8c8', outline='#fdc8c8')

    def blink_alarms(self, box_index):
        state = self.box_states[box_index]

        if state.get('alarm_blink_running'):
            return
        state['alarm_blink_running'] = True

        def _blink():
            st = self.box_states[box_index]

            if not (
                st['alarm1_blinking']
                or st['alarm2_blinking']
                or st['alarm_border_blink']
            ):
                st['alarm_blink_running'] = False
                return

            box_canvas, circle_items, *_ = self.box_data[box_index]
            box_frame = self.box_frames[box_index]

            border_state = st['border_blink_state']
            st['border_blink_state'] = not border_state

            # 🔴 프레임 테두리 깜빡임
            if st['alarm_border_blink']:
                box_frame.config(
                    highlightbackground='#000000' if border_state else '#ff0000'
                )

            if st['alarm1_blinking']:
                fill_now = box_canvas.itemcget(circle_items[0], 'fill')
                box_canvas.itemconfig(
                    circle_items[0],
                    fill='#fdc8c8' if fill_now == 'red' else 'red',
                    outline='#fdc8c8' if fill_now == 'red' else 'red',
                )

            if st['alarm2_blinking']:
                fill_now = box_canvas.itemcget(circle_items[1], 'fill')
                box_canvas.itemconfig(
                    circle_items[1],
                    fill='#fdc8c8' if fill_now == 'red' else 'red',
                    outline='#fdc8c8' if fill_now == 'red' else 'red',
                )

            self.parent.after(self.alarm_blink_interval, _blink)

        _blink()

    def update_fw_status(self, box_index, v_40022, v_40023, v_40024):
        version = v_40022
        error_code = (v_40023 >> 8) & 0xFF
        progress = v_40024 & 0xFF
        remain = (v_40024 >> 8) & 0xFF

        current = (version, error_code, progress, remain, v_40023)
        prev = self.last_fw_status[box_index]
        if prev == current:
            return
        self.last_fw_status[box_index] = current

        upgrading = bool(v_40023 & (1 << 2))
        upgrade_ok = bool(v_40023 & (1 << 0))
        upgrade_fail = bool(v_40023 & (1 << 1))
        rollback_running = bool(v_40023 & (1 << 6))
        rollback_ok = bool(v_40023 & (1 << 4))
        rollback_fail = bool(v_40023 & (1 << 5))

        msg = f'[FW] ver={version}, progress={progress}%, remain={remain}s'
        states = []
        if upgrading:
            states.append('UPGRADING')
        if upgrade_ok:
            states.append('UPGRADE_OK')
        if upgrade_fail:
            states.append(f'UPGRADE_FAIL(err={error_code})')
        if rollback_running:
            states.append('ROLLBACK')
        if rollback_ok:
            states.append('ROLLBACK_OK')
        if rollback_fail:
            states.append(f'ROLLBACK_FAIL(err={error_code})')
        if states:
            msg += ' [' + ', '.join(states) + ']'
        self.console.print(msg)

        self.box_states[box_index]['fw_upgrading'] = upgrading

        if upgrading:
            disp = f"{progress:3d} "
            self.ui_update_queue.put(
                ('segment_display', box_index, disp, False)
            )
            self.ui_update_queue.put(
                ('bar', box_index, progress)
            )
        else:
            self.box_states[box_index]['fw_upgrading'] = False
            if upgrade_ok:
                self.ui_update_queue.put(
                    ('segment_display', box_index, ' End', False)
                )
            elif upgrade_fail or rollback_fail:
                self.ui_update_queue.put(
                    ('segment_display', box_index, 'Err ', True)
                )

    def delayed_load_tftp_ip_from_device(self, box_index: int, delay: float = 1.0):
        time.sleep(delay)
        try:
            self.load_tftp_ip_from_device(box_index)
        except Exception as e:
            self.console.print(
                f'[FW] (ignore) delayed TFTP IP read fail box {box_index}: {e}'
            )

    def load_tftp_ip_from_device(self, box_index: int):
        ip = self.ip_vars[box_index].get()
        client = self.clients.get(ip)
        lock = self.modbus_locks.get(ip)
        if client is None or lock is None:
            return

        addr_ip1 = self.reg_addr(40088)
        try:
            with lock:
                rr = client.read_holding_registers(addr_ip1, 2)
            if isinstance(rr, ExceptionResponse) or rr.isError():
                self.console.print(f'[FW] read 40088/40089 error: {rr}')
                return
            w1, w2 = rr.registers
            a = (w1 >> 8) & 0xFF
            b = w1 & 0xFF
            c = (w2 >> 8) & 0xFF
            d = w2 & 0xFF
            tftp_ip = f'{a}.{b}.{c}.{d}'
            self.tftp_ip_vars[box_index].set(tftp_ip)
            self.console.print(f'[FW] box {box_index} TFTP IP from device: {tftp_ip}')
        except Exception as e:
            msg = str(e)
            if "No response received" in msg:
                self.console.print(
                    f'[FW] box {box_index} ({ip}) TFTP IP read: device not ready yet (ignore).'
                )
            else:
                self.console.print(f'[FW] Error reading TFTP IP for box {box_index} ({ip}): {e}')

    def start_firmware_upgrade(self, box_index: int):
        threading.Thread(
            target=self._do_firmware_upgrade,
            args=(box_index,),
            daemon=True,
        ).start()

    def _do_firmware_upgrade(self, box_index: int):
        ip = self.ip_vars[box_index].get()
        client = self.clients.get(ip)
        lock = self.modbus_locks.get(ip)

        if client is None or lock is None:
            self.console.print(f'[FW] Box {box_index} ({ip}) not connected.')
            self.parent.after(
                0,
                lambda: messagebox.showwarning('FW', '먼저 Modbus 연결을 해주세요.')
            )
            return

        src_path = self.fw_file_paths[box_index]
        if not src_path or not os.path.isfile(src_path):
            self.parent.after(
                0,
                lambda: messagebox.showwarning('FW', 'FW 파일을 먼저 선택해주세요.')
            )
            return

        device_dir = os.path.join(TFTP_ROOT_DIR, TFTP_DEVICE_SUBDIR)
        try:
            os.makedirs(device_dir, exist_ok=True)
        except Exception as e:
            self.console.print(f'[FW] mkdir error: {e}')
            self.parent.after(
                0,
                lambda e=e: messagebox.showerror('FW', f'TFTP 디렉터리 생성에 실패했습니다.\n{e}')
            )
            return

        dst_path = os.path.join(device_dir, TFTP_DEVICE_FILENAME)

        try:
            if os.path.exists(dst_path):
                try:
                    os.remove(dst_path)
                    self.console.print(f'[FW] old TFTP file removed: {dst_path}')
                except PermissionError as e:
                    self.console.print(f'[FW] warning: cannot remove old file: {e}')

            shutil.copyfile(src_path, dst_path)
            self.console.print(
                f'[FW] box {box_index} file copy: {src_path} → {dst_path} '
                f'(RRQ path: {TFTP_DEVICE_SUBDIR}/{TFTP_DEVICE_FILENAME})'
            )
        except Exception as e:
            self.console.print(f'[FW] file copy error: {e}')
            self.parent.after(
                0,
                lambda e=e: messagebox.showerror('FW', f'FW 파일 복사에 실패했습니다.\n{e}')
            )
            return

        tftp_ip_str = self.tftp_ip_vars[box_index].get().strip()
        addr_ip1 = self.reg_addr(40088)
        addr_ctrl = self.reg_addr(40091)

        try:
            with lock:
                try:
                    w1, w2 = encode_ip_to_words(tftp_ip_str)
                    r1 = client.write_registers(addr_ip1, [w1, w2])
                    if isinstance(r1, ExceptionResponse) or r1.isError():
                        self.console.print(f'[FW] write 40088/40089 error (non-fatal): {r1}')
                    else:
                        self.console.print(
                            f'[FW] write 40088/40089 OK (0x{w1:04X}, 0x{w2:04X})'
                        )
                except (ValueError, ModbusIOException, ConnectionException, Exception) as e:
                    self.console.print(f'[FW] write 40088/40089 failed (non-fatal): {e}')

                try:
                    r2 = client.write_register(addr_ctrl, 1)
                    if isinstance(r2, ExceptionResponse) or r2.isError():
                        self.console.print(f'[FW] write 40091 error: {r2}')
                        self.parent.after(
                            0,
                            lambda r2=r2: messagebox.showerror(
                                'FW', f'장비에 FW 시작 명령을 쓰는 데 실패했습니다.\n{r2}'
                            ),
                        )
                        return
                    self.console.print('[FW] write 40091 = 1 OK')
                except Exception as e:
                    self.console.print(
                        f'[FW] write 40091 exception (treated as non-fatal): {e}'
                    )

            self.console.print(
                f"[FW] Upgrade start command sent for box {box_index} ({ip}) via "
                f"TFTP IP='{tftp_ip_str}', file={dst_path}"
            )
            self.parent.after(
                0,
                lambda: messagebox.showinfo(
                    'FW',
                    'FW 업그레이드 명령을 전송했습니다.\n'
                    '※ TFTP IP 레지스터는 장비에 설정된 값을 그대로 사용할 수도 있습니다.',
                ),
            )
        except Exception as e:
            self.console.print(f'[FW] Error starting upgrade for {ip}: {e}')
            self.parent.after(
                0,
                lambda e=e: messagebox.showerror('FW', f'FW 업그레이드 중 오류가 발생했습니다.\n{e}')
            )

    def zero_calibration(self, box_index: int):
        self.console.print(f'[ZERO] button clicked (box_index={box_index})')
        ip = self.ip_vars[box_index].get()
        client = self.clients.get(ip)
        lock = self.modbus_locks.get(ip)

        if client is None or lock is None:
            self.console.print(f'[ZERO] Box {box_index} ({ip}) not connected.')
            messagebox.showwarning('ZERO', '먼저 Modbus 연결을 해주세요.')
            return

        addr = self.reg_addr(40092)
        try:
            with lock:
                r = client.write_register(addr, 1)
                if isinstance(r, ExceptionResponse) or r.isError():
                    self.console.print(f'[ZERO] write 40092=1 error: {r}')
                    messagebox.showerror('ZERO', f'ZERO 명령 전송 실패.\n{r}')
                    return
                self.console.print('[ZERO] write 40092 = 1 OK')

            self.console.print(
                f'[ZERO] Zero calibration command sent for box {box_index} ({ip})'
            )
            messagebox.showinfo('ZERO', 'ZERO 명령을 전송했습니다.')
        except Exception as e:
            self.console.print(f'[ZERO] Error on zero calibration for {ip}: {e}')
            messagebox.showerror('ZERO', f'ZERO 중 오류가 발생했습니다.\n{e}')

    # RST
    def reboot_device(self, box_index: int):
        self.console.print(f'[RST] button clicked (box_index={box_index})')
        ip = self.ip_vars[box_index].get()
        client = self.clients.get(ip)
        lock = self.modbus_locks.get(ip)

        if client is None or lock is None:
            self.console.print(f'[RST] Box {box_index} ({ip}) not connected.')
            messagebox.showwarning('RST', '먼저 Modbus 연결을 해주세요.')
            return

        addr = self.reg_addr(40093)

        def _treat_as_ok(msg: str):
            self.console.print(
                f'[RST] no/invalid response after write (device is rebooting): {msg}'
            )
            messagebox.showinfo(
                'RST',
                '재부팅 명령을 전송했습니다.\n'
                '장비가 재부팅되는 동안 잠시 통신 오류가 발생할 수 있습니다.',
            )

        try:
            with lock:
                r = client.write_register(addr, 1)

            if isinstance(r, ExceptionResponse) or getattr(r, "isError", lambda: False)():
                msg = str(r)
                if "No response received" in msg or "Invalid Message" in msg:
                    _treat_as_ok(msg)
                    return

                self.console.print(f'[RST] write 40093=1 error: {msg}')
                messagebox.showerror('RST', f'RST 명령 전송 실패.\n{msg}')
                return

            self.console.print('[RST] write 40093 = 1 OK')
            messagebox.showinfo('RST', '재부팅 명령을 전송했습니다.')

        except Exception as e:
            msg = str(e)
            if "No response received" in msg or "Invalid Message" in msg:
                _treat_as_ok(msg)
            else:
                self.console.print(f'[RST] Error on reboot for {ip}: {e}')
                messagebox.showerror('RST', f'재부팅 중 오류가 발생했습니다.\n{e}')

    # 램프 설정 팝업 (기존 기능)
    def open_settings_popup(self, box_index: int):
        existing = self.settings_popups[box_index]
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_set()
            return

        win = Toplevel(self.parent)
        win.title(f'Box {box_index + 1} 설정')
        win.configure(bg='#1e1e1e')
        win.resizable(False, False)

        self.settings_popups[box_index] = win

        def on_close():
            self.settings_popups[box_index] = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        Label(
            win,
            text=f'IP: {self.ip_vars[box_index].get()}',
            fg='white',
            bg='#1e1e1e',
            font=('Helvetica', 12, 'bold'),
        ).pack(padx=10, pady=(10, 5))

        Label(
            win,
            text='현재 FW 파일:',
            fg='white',
            bg='#1e1e1e',
            font=('Helvetica', 10),
        ).pack(padx=10, pady=(5, 0))

        Label(
            win,
            textvariable=self.box_states[box_index]['fw_file_name_var'],
            fg='#cccccc',
            bg='#1e1e1e',
            font=('Helvetica', 10),
        ).pack(padx=10, pady=(0, 10))

        btn_frame = Frame(win, bg='#1e1e1e')
        btn_frame.pack(padx=10, pady=10)

        Button(
            btn_frame,
            text='FW 파일 선택',
            command=lambda idx=box_index: self.select_fw_file(idx),
            width=18,
            bg='#555555',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=0, padx=5, pady=5)

        Button(
            btn_frame,
            text='FW 업그레이드 시작',
            command=lambda idx=box_index: self.start_firmware_upgrade(idx),
            width=18,
            bg='#4444aa',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=1, padx=5, pady=5)

        Button(
            btn_frame,
            text='ZERO',
            command=lambda idx=box_index: self.zero_calibration(idx),
            width=18,
            bg='#444444',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=1, column=0, padx=5, pady=5)

        Button(
            btn_frame,
            text='RST',
            command=lambda idx=box_index: self.reboot_device(idx),
            width=18,
            bg='#aa4444',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=1, column=1, padx=5, pady=5)

        Button(
            win,
            text='닫기',
            command=on_close,
            width=10,
            bg='#333333',
            fg='white',
            relief='raised',
            bd=1,
        ).pack(pady=(0, 10))

        win.transient(self.parent)
        win.grab_set()
        win.focus_set()


def main():
    root = Tk()
    root.title('Modbus UI')
    root.geometry('1200x600')
    root.configure(bg='#1e1e1e')

    num_boxes = 4
    gas_types = {
        'modbus_box_0': 'ORG',
        'modbus_box_1': 'ARF-T',
        'modbus_box_2': 'HMDS',
        'modbus_box_3': 'HC-100',
    }

    def alarm_callback(active, box_id):
        if active:
            print(f'[Callback] Alarm active in {box_id}')
        else:
            print(f'[Callback] Alarm cleared in {box_id}')

    modbus_ui = ModbusUI(root, num_boxes, gas_types, alarm_callback)

    row = 0
    col = 0
    max_col = 2
    for frame in modbus_ui.box_frames:
        frame.grid(row=row, column=col, padx=10, pady=10)
        col += 1
        if col >= max_col:
            col = 0
            row += 1

    root.mainloop()


if __name__ == '__main__':
    main()
