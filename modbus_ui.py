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
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


SCALE_FACTOR = 1.65
DEFAULT_TFTP_IP = get_local_ip()
TFTP_FW_BASENAME = 'ASGD3200E.bin'
TFTP_ROOT_DIR = '/srv/tftp'

TFTP_DEVICE_SUBDIR = os.path.join('GDS', 'ASGD-3200')
TFTP_DEVICE_FILENAME = 'asgd3200.bin'


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

    LOG_MAX_ENTRIES = 1000

    @staticmethod
    def reg_addr(addr_4xxxx: int) -> int:
        return addr_4xxxx - 40001

    def __init__(self, parent, num_boxes, gas_types, alarm_callback):
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.virtual_keyboard = VirtualKeyboard(parent)

        self.ip_vars = [StringVar() for _ in range(num_boxes)]
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

        self.last_fw_status = [None] * num_boxes

        self.settings_popups = [None] * num_boxes

        self.log_popups = [None] * num_boxes
        self.log_popup_texts = [None] * num_boxes
        self.box_logs = [[] for _ in range(num_boxes)]

        # TFTP/FW/ZERO/RST 지원 여부 (초기엔 True, 통신 중 자동 판단/갱신)
        self.tftp_supported = [True] * num_boxes
        # FW 상태 레지스터(40023/40024) 지원 여부
        self.fw_status_supported = [True] * num_boxes

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
        self.alarm_blink_interval = 1000  # ★ 알람/에러 램프 깜빡임 1초

        self.start_data_processing_thread()
        self.schedule_ui_update()

    # -------------------- 공통 유틸 --------------------

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

    # -------------------- IP 입력 / UI --------------------

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
        box_frame = Frame(
            self.parent,
            highlightthickness=3,
            highlightbackground='#000000',
            highlightcolor='#000000',
        )
        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(
            inner_frame,
            width=sx(150),
            height=sy(300),
            highlightthickness=sx(1.5),
            highlightbackground='#000000',
            highlightcolor='#000000',
            bg='#1e1e1e',
        )
        box_canvas.pack()
        box_canvas.create_rectangle(0, 0, sx(160), sy(200), fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, sy(200), sx(260), sy(310), fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)

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
                'fw_upgrading': False,
                'alarm_blink_running': False,
                'segment_click_area': (seg_x1, seg_y1, seg_x2, seg_y2),
                'last_log_value': None,
                'last_log_alarm1': None,
                'last_log_alarm2': None,
                'last_log_error_reg': None,
                # ▼ FW 버전 표시용
                'version_text_id': None,
                'last_version_value': None,
                # ▼ 감지기 모델 표시용
                'last_model_str': None,
                'detector_model_str': None,
                # ▼ 알람 모드 (none / al1 / al2)
                'alarm_mode': 'none',
                # ▼ 에러 깜빡이 상태
                'error_blink_running': False,
                'error_blink_state': False,
            }
        )

        def _on_segment_click(event, idx=index):
            self.open_segment_popup(idx)

        box_canvas.tag_bind('segment_click_area', '<Button-1>', _on_segment_click)
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

        # ▼ AL1/AL2/PWR/FUT 램프를 처음부터 OFF 색으로 꽉 채워서 생성
        circle_al1 = box_canvas.create_oval(
            sx(77) - sx(20), sy(200) - sy(32),
            sx(87) - sx(20), sy(190) - sy(32),
            fill=self.LAMP_COLORS_OFF[0],
            outline=self.LAMP_COLORS_OFF[0],
        )
        box_canvas.create_text(
            sx(95) - sx(25),
            sy(222) - sy(40),
            text='AL1',
            fill='#cccccc',
            anchor='e',
        )

        circle_al2 = box_canvas.create_oval(
            sx(133) - sy(30), sy(200) - sy(32),
            sx(123) - sy(30), sy(190) - sy(32),
            fill=self.LAMP_COLORS_OFF[1],
            outline=self.LAMP_COLORS_OFF[1],
        )
        box_canvas.create_text(
            sx(140) - sy(35),
            sy(222) - sy(40),
            text='AL2',
            fill='#cccccc',
            anchor='e',
        )

        circle_pwr = box_canvas.create_oval(
            sx(30) - sx(10), sy(200) - sy(32),
            sx(40) - sy(10), sy(190) - sy(32),
            fill=self.LAMP_COLORS_OFF[2],
            outline=self.LAMP_COLORS_OFF[2],
        )
        box_canvas.create_text(
            sx(35) - sx(10),
            sy(222) - sy(40),
            text='PWR',
            fill='#cccccc',
            anchor='center',
        )

        circle_fut = box_canvas.create_oval(
            sx(171) - sy(40), sy(200) - sy(32),
            sx(181) - sy(40), sy(190) - sy(32),
            fill=self.LAMP_COLORS_OFF[3],
            outline=self.LAMP_COLORS_OFF[3],
        )
        box_canvas.create_text(
            sx(175) - sy(40),
            sy(217) - sy(40),
            text='FUT',
            fill='#cccccc',
            anchor='n',
        )

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

        # ▼ FW 버전 + 감지기 모델 라벨(우측 상단)
        version_text_id = box_canvas.create_text(
            sx(140),
            sy(12),
            text='',
            font=('Helvetica', int(8 * SCALE_FACTOR), 'bold'),
            fill='#cccccc',
            anchor='ne',
        )
        self.box_states[index]['version_text_id'] = version_text_id

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

        # ★ 박스 생성 직후 AL 램프를 OFF 상태로 한 번 확실히 세팅
        self.set_alarm_lamp(
            index,
            alarm1_on=False, blink1=False,
            alarm2_on=False, blink2=False,
        )

    # -------------------- FW 파일 / UI 업데이트 --------------------

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
        """
        states: [AL1, AL2, PWR, FUT]
        AL1/AL2는 알람 로직(set_alarm_lamp)이 관리하므로 여기서는 주로 PWR/FUT만 의미 있게 사용.
        """
        # 에러 깜빡이 중이면 AL 램프는 에러 루틴이 관리하므로 여기서는 PWR/FUT만 건드리도록 한다.
        box_canvas, circle_items, _, _, _ = self.box_data[box_index]
        for i, state in enumerate(states):
            # AL1/AL2는 에러/알람 루틴이 별도로 관리
            if i in (0, 1):
                continue
            color = self.LAMP_COLORS_ON[i] if state else self.LAMP_COLORS_OFF[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)
        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f'modbus_{box_index}')

    def update_segment_display(self, value, box_index=0, blink=False):
        box_canvas = self.box_data[box_index][0]

        # 4자리 오른쪽 정렬, 공백 포함
        value = str(value)
        value = value.rjust(4)[:4]

        prev_val = self.box_states[box_index]['previous_segment_display']
        if value != prev_val:
            self.box_states[box_index]['previous_segment_display'] = value

        leading_zero = True
        for idx, digit in enumerate(value):
            if digit == ' ':
                segments = SEGMENTS[' ']
            elif leading_zero and digit == '0' and idx < 3:
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

    # -------------------- 연결 / 연결토글 --------------------

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
                # 기본값: 먼저 True로 두고, capability probe로 확정
                self.tftp_supported[i] = True
                self.fw_status_supported[i] = True
                self.last_fw_status[i] = None
                self.box_states[i]['fw_upgrading'] = False

                # ★ 별도의 임시 클라이언트로 확장 레지스터 지원 여부만 사전 검사
                try:
                    self.detect_device_capabilities(ip, i)
                except Exception as e:
                    self.console.print(
                        f'[FW] box {i} ({ip}) capability probe failed (ignore, fallback 동작): {e}'
                    )

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

                # ✅ 감지기 모델(40030~40033) 1회 읽기(미지원이면 조용히 무시)
                threading.Thread(
                    target=self.delayed_load_detector_model,
                    args=(i, 1.0),
                    daemon=True
                ).start()

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
        self.box_states[i]['fw_upgrading'] = False
        self.last_fw_status[i] = None

        self.reset_ui_elements(i)
        self.action_buttons[i].config(
            image=self.connect_image,
            relief='flat',
            borderwidth=0,
        )
        self.entries[i].config(state='normal')
        self.box_frames[i].config(highlightbackground='#000000')
        if manual:
            box_canvas = self.box_data[i][0]
            gms1000_id = self.box_states[i]['gms1000_text_id']
            box_canvas.itemconfig(gms1000_id, state='normal')
            self.disconnection_labels[i].grid_remove()
            self.reconnect_attempt_labels[i].grid_remove()

    def reset_ui_elements(self, box_index):
        """
        연결 끊김/수동 disconnect 시 박스 전체 UI/알람 상태 초기화.
        (통신 단절 시 AL1/AL2/테두리 깜빡임 포함 전부 OFF)
        """
        state = self.box_states[box_index]

        # ★ 알람 관련 상태 싹 초기화
        state['alarm1_on'] = False
        state['alarm2_on'] = False
        state['alarm1_blinking'] = False
        state['alarm2_blinking'] = False
        state['alarm_border_blink'] = False
        state['alarm_blink_running'] = False
        state['border_blink_state'] = False
        state['alarm_mode'] = 'none'

        # 램프도 실제로 OFF로 반영
        try:
            self.set_alarm_lamp(
                box_index,
                alarm1_on=False,
                blink1=False,
                alarm2_on=False,
                blink2=False,
            )
        except Exception:
            pass

        # 테두리 색 기본값으로
        if 0 <= box_index < len(self.box_frames):
            self.box_frames[box_index].config(highlightbackground='#000000')

        # 공통 램프/세그먼트/바 초기화
        self.update_circle_state([False, False, False, False], box_index=box_index)
        self.update_segment_display('    ', box_index=box_index)
        self.show_bar(box_index, show=False)

        # ▼ FW 버전 라벨 초기화
        state['last_version_value'] = None
        version_text_id = state.get('version_text_id')
        if version_text_id is not None:
            box_canvas = self.box_data[box_index][0]
            box_canvas.itemconfig(version_text_id, text='')

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

    # -------------------- 장비 능력(확장 레지스터) 사전검출 --------------------

    def detect_device_capabilities(self, ip: str, box_index: int):
        """
        실제 통신에 사용하는 client와는 별도의 임시 클라이언트로
        40001~40024 읽기를 시도해서 확장 레지스터 지원 여부를 미리 판단한다.

        - 기본 레지스터(40001~40022) 읽기 OK 기준으로 연결 확인
        - 40001~40024 읽기:
          - 24개 이상 레지스터 응답 → 신형(확장 레지스터 지원)
          - 에러/22개만 응답 → 구형(확장 레지스터 미지원, FW/TFTP/ZERO/RST 비활성화)
        """
        tmp_client = ModbusTcpClient(ip, port=502, timeout=2)
        try:
            if not tmp_client.connect():
                self.console.print(
                    f'[FW] box {box_index} ({ip}) : capability probe connect fail → '
                    f'확장 레지스터 미지원 장비로 가정 (FW/TFTP/ZERO/RST 비활성화).'
                )
                self.fw_status_supported[box_index] = False
                self.tftp_supported[box_index] = False
                return

            start_address = self.reg_addr(40001)
            BASE_REG_COUNT = 22

            rr_base = tmp_client.read_holding_registers(start_address, BASE_REG_COUNT)
            if isinstance(rr_base, ExceptionResponse) or rr_base.isError():
                raise ModbusIOException(
                    f'Error reading base regs 40001~40022 from {ip} during capability probe: {rr_base}'
                )
            regs_base = getattr(rr_base, "registers", []) or []
            if len(regs_base) < BASE_REG_COUNT:
                raise ModbusIOException(
                    f'Base regs length < {BASE_REG_COUNT} during capability probe (got {len(regs_base)})'
                )

            rr_ext = tmp_client.read_holding_registers(start_address, 24)
            if isinstance(rr_ext, ExceptionResponse) or rr_ext.isError():
                self.console.print(
                    f'[FW] box {box_index} ({ip}) : capability probe 결과 → '
                    f'40023/40024 읽기 에러 → 구형 장비 (FW/TFTP/ZERO/RST 비활성화). ({rr_ext})'
                )
                self.fw_status_supported[box_index] = False
                self.tftp_supported[box_index] = False
                return

            regs_ext = getattr(rr_ext, "registers", []) or []
            if len(regs_ext) >= 24:
                self.console.print(
                    f'[FW] box {box_index} ({ip}) : capability probe 결과 → '
                    f'40023/40024 포함 확장 레지스터 지원 장비로 판단.'
                )
                self.fw_status_supported[box_index] = True
                self.tftp_supported[box_index] = True
            else:
                self.console.print(
                    f'[FW] box {box_index} ({ip}) : capability probe 결과 → '
                    f'24개 미만 응답(={len(regs_ext)}) → 구형 장비 (FW/TFTP/ZERO/RST 비활성화).'
                )
                self.fw_status_supported[box_index] = False
                self.tftp_supported[box_index] = False

        finally:
            try:
                tmp_client.close()
            except Exception:
                pass

    # -------------------- 데이터 읽기 쓰레드 --------------------

    def read_modbus_data(self, ip, client, stop_flag, box_index):
        start_address = self.reg_addr(40001)
        BASE_REG_COUNT = 22  # 40001 ~ 40022
        last_model_read = 0.0

        while not stop_flag.is_set():
            try:
                if client is None or not client.is_socket_open():
                    raise ConnectionException('Socket is closed')

                lock = self.modbus_locks.get(ip)
                if lock is None:
                    break

                if self.fw_status_supported[box_index]:
                    num_registers = 24
                else:
                    num_registers = BASE_REG_COUNT

                with lock:
                    response = client.read_holding_registers(start_address, num_registers)

                if isinstance(response, ExceptionResponse) or response.isError():
                    raise ModbusIOException(
                        f'Error reading from {ip}, address 40001~400{num_registers}'
                    )

                raw_regs = getattr(response, "registers", []) or []

                if len(raw_regs) < BASE_REG_COUNT:
                    raise ModbusIOException(
                        f'Error reading from {ip}: expected at least {BASE_REG_COUNT} regs, got {len(raw_regs)}'
                    )

                value_40023 = None
                value_40024 = None

                if self.fw_status_supported[box_index] and len(raw_regs) < 24:
                    self.console.print(
                        f'[FW] box {box_index} ({ip}) : 40023/40024 레지스터가 없는 장비로 판단 → '
                        f'FW 상태/TFTP/ZERO/RST 기능 비활성화.'
                    )
                    self.fw_status_supported[box_index] = False
                    self.tftp_supported[box_index] = False
                elif self.fw_status_supported[box_index] and len(raw_regs) >= 24:
                    value_40023 = raw_regs[22]
                    value_40024 = raw_regs[23]

                value_40001 = raw_regs[0]
                value_40005 = raw_regs[4]
                value_40007 = raw_regs[7]
                value_40011 = raw_regs[10]
                value_40022 = raw_regs[21]

                # ▼ 버전 정보(40022) UI 전달
                self.ui_update_queue.put(('version', box_index, value_40022))

                # (선택) 감지기 모델(40030~40033) 5초마다 갱신 시도
                now = time.monotonic()
                if now - last_model_read > 5.0:
                    last_model_read = now
                    try:
                        self.read_detector_model_from_device(box_index)
                    except Exception:
                        pass

                bit_6_on = bool(value_40001 & (1 << 6))
                bit_7_on = bool(value_40001 & (1 << 7))
                self.box_states[box_index]['alarm1_on'] = bit_6_on
                self.box_states[box_index]['alarm2_on'] = bit_7_on
                self.ui_update_queue.put(('alarm_check', box_index))

                self.maybe_log_event(
                    box_index,
                    value_40005,
                    bit_6_on,
                    bit_7_on,
                    value_40007,
                )

                bits = [bool(value_40007 & (1 << n)) for n in range(4)]
                if not any(bits):
                    if self.box_states[box_index]['blinking_error']:
                        self.box_states[box_index]['blinking_error'] = False
                        self.ui_update_queue.put(('error_off', box_index))

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
                        if not self.box_states[box_index]['blinking_error']:
                            self.box_states[box_index]['blinking_error'] = True
                            self.ui_update_queue.put(('error_on', box_index))
                        self.data_queue.put((box_index, error_display, True))
                    else:
                        if self.box_states[box_index]['blinking_error']:
                            self.box_states[box_index]['blinking_error'] = False
                            self.ui_update_queue.put(('error_off', box_index))
                        self.data_queue.put((box_index, error_display, False))

                if not self.box_states[box_index].get('fw_upgrading', False):
                    self.ui_update_queue.put(('bar', box_index, value_40011))

                if self.fw_status_supported[box_index] and value_40023 is not None and value_40024 is not None:
                    self.ui_update_queue.put(
                        ('fw_status', box_index, value_40022, value_40023, value_40024)
                    )

                time.sleep(self.communication_interval)

            except ConnectionException as e:
                self.console.print(f'Connection to {ip} lost: {e}')

                if self.box_states[box_index].get('fw_upgrading', False):
                    self.console.print(
                        f'[FW] box {box_index} disconnected during upgrade (expected).'
                    )
                    self.box_states[box_index]['fw_upgrading'] = False
                    self.last_fw_status[box_index] = None
                    self.ui_update_queue.put(('bar', box_index, 0))
                    self.ui_update_queue.put(('segment_display', box_index, '    ', False))
                else:
                    self.handle_disconnection(box_index)

                self.reconnect(ip, client, stop_flag, box_index)
                break

            except ModbusIOException as e:
                msg = str(e)

                if self.fw_status_supported[box_index] and '40001~40024' in msg:
                    self.console.print(
                        f'[Modbus] box {box_index} ({ip}) : 40023/40024 등 확장 레지스터 미지원으로 판단, '
                        f'FW/TFTP/ZERO/RST 기능을 비활성화하고 통신은 계속 진행합니다. ({e})'
                    )
                    self.fw_status_supported[box_index] = False
                    self.tftp_supported[box_index] = False
                    time.sleep(self.communication_interval * 2)
                    continue

                self.console.print(
                    f'Temporary Modbus I/O error from {ip}: {e}. Will retry...'
                )
                time.sleep(self.communication_interval * 2)
                continue

            except Exception as e:
                msg = str(e)

                decode_keywords = [
                    "unpack requires a buffer of 4 bytes",
                    "Unable to decode response",
                    "No response received",
                ]
                if any(k in msg for k in decode_keywords):
                    self.console.print(
                        f"[Modbus] decode error from {ip}: {e}. Treat as connection lost → reconnect."
                    )

                    if self.box_states[box_index].get('fw_upgrading', False):
                        self.console.print(
                            f'[FW] box {box_index} disconnected during upgrade (expected).'
                        )
                        self.box_states[box_index]['fw_upgrading'] = False
                        self.last_fw_status[box_index] = None
                        self.ui_update_queue.put(('bar', box_index, 0))
                        self.ui_update_queue.put(('segment_display', box_index, '    ', False))
                    else:
                        self.handle_disconnection(box_index)

                    self.reconnect(ip, client, stop_flag, box_index)
                    break

                self.console.print(f'Unexpected error reading data from {ip}: {e}')
                self.handle_disconnection(box_index)
                self.reconnect(ip, client, stop_flag, box_index)
                break

    # -------------------- 로그 --------------------

    def maybe_log_event(self, box_index, value_40005, alarm1, alarm2, error_reg):
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
            return

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
            elif typ == 'version':
                _, box_index, version = item
                self.set_version_label(box_index, version)
            elif typ == 'detector_model':
                _, box_index, model = item
                self.set_detector_model(box_index, model)
            elif typ == 'fw_status':
                _, box_index, v_40022, v_40023, v_40024 = item
                if self.fw_status_supported[box_index]:
                    self.update_fw_status(box_index, v_40022, v_40023, v_40024)
            elif typ == 'error_on':
                _, box_index = item
                self.start_error_blink(box_index)
            elif typ == 'error_off':
                _, box_index = item
                self.stop_error_blink(box_index)

        self.schedule_ui_update()

    # -------------------- 로그 팝업 --------------------

    def open_segment_popup(self, box_index: int):
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

        self.refresh_log_view(box_index)

        def _auto_refresh():
            if win.winfo_exists() and self.log_popups[box_index] is win:
                self.refresh_log_view(box_index)
                win.after(1000, _auto_refresh)

        win.after(1000, _auto_refresh)

        win.transient(self.parent)

        def _safe_grab():
            try:
                if win.winfo_exists() and win.winfo_viewable():
                    win.grab_set()
                    win.focus_set()
            except Exception as e:
                if hasattr(self, "console"):
                    self.console.print(f"[UI] segment popup grab_set skipped: {e}")

        win.after(50, _safe_grab)

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

    # -------------------- 연결 끊김 / 재연결 --------------------

    def handle_disconnection(self, box_index):
        self.disconnection_counts[box_index] += 1
        count = self.disconnection_counts[box_index]
        self.parent.after(
            0,
            lambda idx=box_index, c=count: self.disconnection_labels[idx].config(
                text=f'DC: {c}'
            ),
        )

        self.box_states[box_index]['fw_upgrading'] = False
        self.last_fw_status[box_index] = None

        # ▼ 알람/램프/세그먼트/바 한 번에 초기화 (여기서만 처리)
        self.parent.after(
            0,
            lambda idx=box_index: self.reset_ui_elements(idx)
        )

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
                highlightbackground='#000000',
            ),
        )

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
                    try:
                        if client is not None:
                            client.close()
                    except Exception:
                        pass

                    self.clients[ip] = new_client
                    client = new_client

                    if ip not in self.modbus_locks:
                        self.modbus_locks[ip] = threading.Lock()

                    self.last_fw_status[box_index] = None
                    self.box_states[box_index]['fw_upgrading'] = False

                    try:
                        self.detect_device_capabilities(ip, box_index)
                        self.console.print(
                            f'[FW] box {box_index} ({ip}) : reconnect 성공 '
                            f'(fw_status_supported={self.fw_status_supported[box_index]}, '
                            f'tftp_supported={self.tftp_supported[box_index]})'
                        )
                    except Exception as e:
                        self.console.print(
                            f'[FW] box {box_index} ({ip}) : reconnect 후 capability probe 실패 '
                            f'(fallback 동작, 기존 플래그 유지). {e}'
                        )

                    stop_flag.clear()
                    t = threading.Thread(
                        target=self.read_modbus_data,
                        args=(ip, new_client, stop_flag, box_index),
                        daemon=True,
                    )
                    self.connected_clients[ip] = t
                    t.start()

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
                            highlightbackground='#000000'
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

    # -------------------- 알람 처리 --------------------

    def check_alarms(self, box_index):
        """
        AL1/AL2 알람 상태를 모드 기반으로 관리:
        - AL2가 잡히면 AL2 모드 우선
        - AL2 모드일 때는 AL2만 깜빡, AL1은 항상 켜져 있어야 함
        - AL1만 잡힐 때는 AL1만 깜빡
        - 알람이 없으면 둘 다 OFF
        """
        state = self.box_states[box_index]

        # ★ 에러 코드 깜빡이 중이면 알람 램프는 에러 패턴이 우선이므로 건드리지 않는다.
        if state.get('blinking_error'):
            return

        # 레지스터에서 읽어온 원본 알람 상태
        alarm1_raw = state['alarm1_on']
        alarm2_raw = state['alarm2_on']

        # 현재 모드 결정: AL2가 잡히면 AL2 우선
        if alarm2_raw:
            new_mode = 'al2'
        elif alarm1_raw:
            new_mode = 'al1'
        else:
            new_mode = 'none'

        prev_mode = state.get('alarm_mode', 'none')

        # 모드 저장
        state['alarm_mode'] = new_mode

        # --- 알람 없음 모드 ---
        if new_mode == 'none':
            state['alarm1_blinking'] = False
            state['alarm2_blinking'] = False
            state['alarm_border_blink'] = False
            state['alarm_blink_running'] = False

            self.set_alarm_lamp(
                box_index,
                alarm1_on=False,
                blink1=False,
                alarm2_on=False,
                blink2=False,
            )
            box_frame = self.box_frames[box_index]
            box_frame.config(highlightbackground='#000000')
            state['border_blink_state'] = False
            return

        # 모드가 그대로고 블링크 루프도 돌고 있으면 그대로 유지
        # (색을 다시 리셋하지 않아야 깜빡임 주기가 일정하게 보임)
        if new_mode == prev_mode and state.get('alarm_blink_running', False):
            return

        # --- AL2 모드: AL2 우선, AL2 깜빡 + AL1 항상 켜져 있어야 함 ---
        if new_mode == 'al2':
            # AL2 활성일 때는 AL1은 무조건 ON(점등) 상태로 강제
            state['alarm1_on'] = True       # 논리 상태도 ON
            state['alarm1_blinking'] = False
            state['alarm2_blinking'] = True
            state['alarm_border_blink'] = True

            # 초기 색 설정: AL1=빨간 고정, AL2=빨간 (이후 blink_alarms에서 AL2만 깜빡)
            self.set_alarm_lamp(
                box_index,
                alarm1_on=True,  blink1=False,   # AL1: 고정 빨강
                alarm2_on=True,  blink2=False,   # AL2: 빨강에서 시작
            )

        # --- AL1 단독 모드: AL1만 깜빡, AL2는 꺼짐 ---
        elif new_mode == 'al1':
            state['alarm1_blinking'] = True
            state['alarm2_blinking'] = False
            state['alarm_border_blink'] = True

            # 초기 색: AL1 = 빨강 (이후 깜빡), AL2 = OFF
            self.set_alarm_lamp(
                box_index,
                alarm1_on=True,  blink1=False,
                alarm2_on=False, blink2=False,
            )

        # 블링크 루프가 안 돌고 있으면 시작
        if not state.get('alarm_blink_running'):
            self.blink_alarms(box_index)

    def set_alarm_lamp(self, box_index, alarm1_on, blink1, alarm2_on, blink2):
        box_canvas, circle_items, *_ = self.box_data[box_index]
        # AL1
        if alarm1_on:
            if blink1:
                box_canvas.itemconfig(circle_items[0], fill='#fdc8c8', outline='#fdc8c8')
            else:
                box_canvas.itemconfig(circle_items[0], fill='red', outline='red')
        else:
            box_canvas.itemconfig(circle_items[0], fill='#fdc8c8', outline='#fdc8c8')

        # AL2
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

            # ★ 연결이 끊겼으면 알람 깜빡임 즉시 종료 + 램프 OFF
            if self.ip_vars[box_index].get() not in self.connected_clients:
                self.set_alarm_lamp(box_index, False, False, False, False)
                st['alarm_blink_running'] = False
                st['alarm1_blinking'] = False
                st['alarm2_blinking'] = False
                st['alarm_border_blink'] = False
                self.box_frames[box_index].config(highlightbackground='#000000')
                return

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

    # -------------------- 에러 깜빡 처리 (AL1 상시, AL2+FUT 1초 깜빡) --------------------

    def start_error_blink(self, box_index: int):
        state = self.box_states[box_index]

        # 이미 돌고 있으면 그대로
        if state.get('error_blink_running'):
            return

        state['error_blink_running'] = True
        state['error_blink_state'] = False

        # 에러일 때는 알람 모드/블링크는 모두 비활성화 (에러 패턴이 우선)
        state['alarm1_blinking'] = False
        state['alarm2_blinking'] = False
        state['alarm_border_blink'] = False
        state['alarm_blink_running'] = False
        state['alarm_mode'] = 'none'

        box_canvas, circle_items, *_ = self.box_data[box_index]

        # AL1 상시 빨간색 ON
        box_canvas.itemconfig(circle_items[0], fill='red', outline='red')

        # 처음 한 번 AL2/FUT를 ON으로 시작
        box_canvas.itemconfig(circle_items[1], fill='red', outline='red')
        box_canvas.itemconfig(circle_items[3], fill='yellow', outline='yellow')

        def _blink():
            st = self.box_states[box_index]
            if not st.get('error_blink_running'):
                return

            box_canvas, circle_items, *_ = self.box_data[box_index]

            # 토글 상태 반전
            st['error_blink_state'] = not st['error_blink_state']

            if st['error_blink_state']:
                # ON 상태
                box_canvas.itemconfig(circle_items[1], fill='red', outline='red')      # AL2
                box_canvas.itemconfig(circle_items[3], fill='yellow', outline='yellow')  # FUT
            else:
                # OFF 상태
                box_canvas.itemconfig(circle_items[1],
                                      fill=self.LAMP_COLORS_OFF[1],
                                      outline=self.LAMP_COLORS_OFF[1])
                box_canvas.itemconfig(circle_items[3],
                                      fill=self.LAMP_COLORS_OFF[3],
                                      outline=self.LAMP_COLORS_OFF[3])

            self.parent.after(self.alarm_blink_interval, _blink)

        _blink()

    def stop_error_blink(self, box_index: int):
        state = self.box_states[box_index]
        state['error_blink_running'] = False
        state['error_blink_state'] = False

        # 에러 종료 시 AL 램프는 잠시 모두 OFF로 초기화
        box_canvas, circle_items, *_ = self.box_data[box_index]
        box_canvas.itemconfig(circle_items[0],
                              fill=self.LAMP_COLORS_OFF[0],
                              outline=self.LAMP_COLORS_OFF[0])
        box_canvas.itemconfig(circle_items[1],
                              fill=self.LAMP_COLORS_OFF[1],
                              outline=self.LAMP_COLORS_OFF[1])
        box_canvas.itemconfig(circle_items[3],
                              fill=self.LAMP_COLORS_OFF[3],
                              outline=self.LAMP_COLORS_OFF[3])

        # 이후 주기적으로 들어오는 alarm_check 에서 실제 AL1/AL2 상태에 맞춰 다시 조정된다.

    # -------------------- FW 상태 표시/업데이트 --------------------

    def update_fw_status(self, box_index, v_40022, v_40023, v_40024):
        # FW 상태 레지스터 미지원 장비라면 아무 것도 하지 않음
        if not self.fw_status_supported[box_index]:
            return

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

        msg = (
            f'[FW] box {box_index} ver={version}, '
            f'progress={progress}%, remain={remain}s'
        )
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
            disp = f"{progress:4d}"
            self.ui_update_queue.put(
                ('segment_display', box_index, disp, False)
            )
            self.ui_update_queue.put(
                ('bar', box_index, progress)
            )
        else:
            if upgrade_ok:
                self.ui_update_queue.put(
                    ('segment_display', box_index, ' End', False)
                )
            elif upgrade_fail or rollback_fail:
                self.ui_update_queue.put(
                    ('segment_display', box_index, 'Err ', True)
                )

    # -------------------- TFTP IP 읽기 --------------------

    def delayed_load_tftp_ip_from_device(self, box_index: int, delay: float = 1.0):
        if not self.tftp_supported[box_index]:
            return

        time.sleep(delay)

        if not self.tftp_supported[box_index]:
            return

        try:
            self.load_tftp_ip_from_device(box_index)
        except Exception as e:
            self.console.print(
                f'[FW] (ignore) delayed TFTP IP read fail box {box_index}: {e}'
            )

    def load_tftp_ip_from_device(self, box_index: int):
        if not self.tftp_supported[box_index]:
            return

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
                msg = str(rr)
                self.console.print(f'[FW] read 40088/40089 error: {rr}')
                self.console.print(
                    f"[FW] box {box_index} ({ip}) : TFTP IP 레지스터 접근 오류 발생 → "
                    f"이후 이 박스에 대해서는 자동 TFTP 기능 비활성화."
                )
                self.tftp_supported[box_index] = False
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
                    f'[FW] box {box_index} ({ip}) TFTP IP read: device not ready (No response). '
                    f'해당 장비에 대해서는 자동 TFTP 기능을 비활성화합니다.'
                )
                self.tftp_supported[box_index] = False
            else:
                self.console.print(f'[FW] Error reading TFTP IP for box {box_index} ({ip}): {e}')
                if 'Failed to connect' in msg or 'Socket is closed' in msg:
                    self.console.print(
                        f'[FW] box {box_index} ({ip}) : TFTP 접근 시 연결 문제 발생 → 이후 자동 TFTP IP 읽기 비활성화.'
                    )
                    self.tftp_supported[box_index] = False

    # -------------------- FW 업그레이드 --------------------

    def start_firmware_upgrade(self, box_index: int):
        # 이 박스가 TFTP/FW 기능을 지원하지 않는다고 판단되면 애초에 pass
        if not self.tftp_supported[box_index]:
            self.console.print(
                f'[FW] box {box_index} : TFTP/FW 기능 미지원으로 FW 업그레이드 요청을 무시합니다.'
            )
            messagebox.showwarning(
                'FW',
                '이 장치는 TFTP/FW 기능을 지원하지 않는 것으로 판단되어,\n'
                'FW 업그레이드를 수행하지 않습니다.'
            )
            return

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

            self.box_states[box_index]['fw_upgrading'] = True
            self.console.print(f'[FW] box {box_index} : local fw_upgrading = True')

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

    # -------------------- ZERO / RST --------------------

    def zero_calibration(self, box_index: int):
        self.console.print(f'[ZERO] button clicked (box_index={box_index})')

        # 이 박스가 ZERO(40092)를 지원하지 않는다고 판단되면 애초에 pass
        if not self.tftp_supported[box_index]:
            self.console.print(
                f'[ZERO] box {box_index} : ZERO 기능(40092) 미지원으로 판단, 명령 전송을 무시합니다.'
            )
            messagebox.showwarning(
                'ZERO',
                '이 장치는 ZERO 명령(40092)을 지원하지 않는 것으로 판단되어,\n'
                'ZERO 기능을 수행하지 않습니다.'
            )
            return

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

    def reboot_device(self, box_index: int):
        self.console.print(f'[RST] button clicked (box_index={box_index})')

        # 이 박스가 RST(40093)를 지원하지 않는다고 판단되면 애초에 pass
        if not self.tftp_supported[box_index]:
            self.console.print(
                f'[RST] box {box_index} : 재부팅 기능(40093) 미지원으로 판단, 명령 전송을 무시합니다.'
            )
            messagebox.showwarning(
                'RST',
                '이 장치는 재부팅 명령(40093)을 지원하지 않는 것으로 판단되어,\n'
                'RST 기능을 수행하지 않습니다.'
            )
            return

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



    # ---------- 감지기 모델 표시(40030~40033) ----------

    # ✅ 장비마다 다를 수 있음: "감지기 모델 문자열"이 들어있는 레지스터 시작 주소
    DETECTOR_MODEL_REG_START = 40030   # 40030~40033 (총 4워드 = 8바이트 가정)
    DETECTOR_MODEL_REG_COUNT = 4

    def delayed_load_detector_model(self, box_index: int, delay: float = 1.0):
        """연결 직후 장비가 준비될 시간을 조금 주고 모델 읽기"""
        time.sleep(delay)
        try:
            self.read_detector_model_from_device(box_index)
        except Exception:
            pass

    def read_detector_model_from_device(self, box_index: int):
        """
        40030~40033에서 모델 문자열을 읽어 UI에 반영.
        - 4워드(8바이트)를 big-endian 바이트로 변환해서 ASCII로 디코드하는 방식 가정
        - 장비 포맷이 다르면 decode 부분만 수정
        """
        ip = self.ip_vars[box_index].get()
        client = self.clients.get(ip)
        lock = self.modbus_locks.get(ip)
        if client is None or lock is None:
            return

        addr = self.reg_addr(self.DETECTOR_MODEL_REG_START)

        with lock:
            rr = client.read_holding_registers(addr, self.DETECTOR_MODEL_REG_COUNT)

        if isinstance(rr, ExceptionResponse) or rr.isError():
            return

        regs = getattr(rr, "registers", []) or []
        if len(regs) < self.DETECTOR_MODEL_REG_COUNT:
            return

        # ✅ 워드 -> 바이트(빅엔디안) -> ASCII
        raw = bytearray()
        for w in regs:
            raw.append((w >> 8) & 0xFF)
            raw.append(w & 0xFF)

        try:
            model = raw.decode("ascii", errors="ignore")
        except Exception:
            model = ""

        model = model.replace("\x00", "").strip()
        if not model:
            return

        # UI 스레드로 전달
        self.ui_update_queue.put(("detector_model", box_index, model))


    # -------------------- 설정 팝업 --------------------

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
            command=lambda idx=box_index: self.start_firmware_upgrade(idx) if hasattr(self, "start_firmware_upgrade") else messagebox.showwarning("FW", "start_firmware_upgrade()가 코드에 없습니다."),
            width=18,
            bg='#4444aa',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=0, column=1, padx=5, pady=5)

        Button(
            btn_frame,
            text='ZERO',
            command=lambda idx=box_index: self.zero_calibration(idx) if hasattr(self, "zero_calibration") else messagebox.showwarning("ZERO", "zero_calibration()가 코드에 없습니다."),
            width=18,
            bg='#444444',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=1, column=0, padx=5, pady=5)

        Button(
            btn_frame,
            text='RST',
            command=lambda idx=box_index: self.reboot_device(idx) if hasattr(self, "reboot_device") else messagebox.showwarning("RST", "reboot_device()가 코드에 없습니다."),
            width=18,
            bg='#aa4444',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=1, column=1, padx=5, pady=5)

        # ✅ 모델 변경 버튼(요청: ZERO/RST 팝업에 추가)
        def _confirm_set_model(bit0, name):
            if messagebox.askyesno('모델 변경', f'{name} 로 변경할까요?\n(장비 적용/재시작이 필요할 수 있습니다)'):
                self.change_device_model(box_index, bit0)

        Button(
            btn_frame,
            text='ASGD3200로 변경',
            command=lambda: _confirm_set_model(0, 'ASGD3200'),
            width=18,
            bg='#333333',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=2, column=0, padx=5, pady=(10, 5))

        Button(
            btn_frame,
            text='ASGD3210로 변경',
            command=lambda: _confirm_set_model(1, 'ASGD3210'),
            width=18,
            bg='#333333',
            fg='white',
            relief='raised',
            bd=1,
        ).grid(row=2, column=1, padx=5, pady=(10, 5))

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

        def _safe_grab():
            try:
                if win.winfo_exists() and win.winfo_viewable():
                    win.grab_set()
                    win.focus_set()
            except Exception as e:
                if hasattr(self, "console"):
                    self.console.print(f"[UI] settings popup grab_set skipped: {e}")

        win.after(50, _safe_grab)

    # ---------- FW 버전 표시 관련 유틸 ----------

    def format_version(self, version: int) -> str:
        """
        40022 값을 보기 좋게 포맷.
        예) 123 -> v1.23, 100 -> v1.00
        장비 프로토콜이 다르면 여기만 수정하면 됨.
        """
        try:
            v = int(version)
        except Exception:
            return f'v{version}'

        major = v // 100
        minor = v % 100
        return f'v{major}.{minor:02d}'

    def set_version_label(self, box_index: int, version: int):
        state = self.box_states[box_index]
        # 이전 값과 같으면 갱신 안 함
        if state.get('last_version_value') == version:
            return

        state['last_version_value'] = version
        version_text_id = state.get('version_text_id')
        if version_text_id is None:
            return

        box_canvas = self.box_data[box_index][0]
        text = self.format_version(version)
        box_canvas.itemconfig(version_text_id, text=text)


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
