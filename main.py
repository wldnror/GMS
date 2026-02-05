
# main.py

import json
import os
import time
from tkinter import Tk, Frame, Button, Label, Entry, messagebox, StringVar, Toplevel
from tkinter import ttk
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
from ups_monitor_ui import UPSMonitorUI
import threading
import psutil
import signal
import sys
import subprocess
import socket
from settings import show_settings, prompt_new_password, show_password_prompt, load_settings, save_settings, initialize_globals
import utils
import tkinter as tk
import pygame
import datetime
import locale
import RPi.GPIO as GPIO

os.environ['DISPLAY'] = ':0'

locale.setlocale(locale.LC_TIME, 'ko_KR.UTF-8')

SETTINGS_FILE = "settings.json"

key = utils.load_key()
cipher_suite = utils.cipher_suite

# GPIO 설정
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# 사용할 핀 번호 설정
RED_PIN = 20  # 빨강 LED에 대응하는 핀
YELLOW_PIN = 21  # 노랑 LED에 대응하는 핀

# 출력 핀으로 설정
GPIO.setup(RED_PIN, GPIO.OUT)
GPIO.setup(YELLOW_PIN, GPIO.OUT)

# 초기값 설정
GPIO.output(RED_PIN, GPIO.LOW)
GPIO.output(YELLOW_PIN, GPIO.LOW)

def encrypt_data(data):
    return utils.encrypt_data(data)

def decrypt_data(data):
    return utils.decrypt_data(data)

settings = load_settings()
admin_password = settings.get("admin_password")

ignore_commit = None
update_notification_frame = None
checking_updates = True
branch_window = None

# 전역 알람 상태 변수
global_alarm_active = False
global_fut_active = False
alarm_blinking = False
fut_blinking = False

selected_audio_file = settings.get("audio_file")
audio_playing = False

# 각 상자의 알람 상태를 저장하는 딕셔너리
box_alarm_states = {}

pygame.mixer.init()

def play_alarm_sound():
    global selected_audio_file, audio_playing
    if selected_audio_file is None:
        print("No audio file selected. Skipping alarm sound.")
        return

    if not audio_playing:
        if os.path.isfile(selected_audio_file):
            try:
                pygame.mixer.music.load(selected_audio_file)
                pygame.mixer.music.play(loops=-1)
                audio_playing = True
            except pygame.error as e:
                print(f"Pygame error: {e}")
        else:
            print(f"File not found: {selected_audio_file}")

def stop_alarm_sound():
    global audio_playing
    if audio_playing:
        pygame.mixer.music.stop()
        audio_playing = False

def set_alarm_status(active, box_id, fut=False):
    global global_alarm_active, global_fut_active, alarm_blinking, fut_blinking
    # 이전 상태 저장
    prev_state = box_alarm_states.get(box_id, {'active': False, 'fut': False})
    prev_global_alarm_active = global_alarm_active
    prev_global_fut_active = global_fut_active

    # 새로운 상태 저장
    box_alarm_states[box_id] = {'active': active, 'fut': fut}

    # 전체 알람 상태 업데이트
    global_alarm_active = any(state['active'] for state in box_alarm_states.values())
    global_fut_active = any(state['fut'] for state in box_alarm_states.values())

    # 상태 변경 여부 확인
    state_changed = (prev_state['active'] != active) or (prev_state['fut'] != fut)
    global_state_changed = (prev_global_alarm_active != global_alarm_active) or (prev_global_fut_active != global_fut_active)

    if global_fut_active:
        if not prev_global_fut_active:
            stop_alarm_sound()
            alarm_blinking = False
            start_fut_blinking()
    elif global_alarm_active:
        if not prev_global_alarm_active:
            play_alarm_sound()
            start_alarm_blinking()
    else:
        if prev_global_alarm_active or prev_global_fut_active:
            stop_all_alarms()

def start_alarm_blinking():
    global alarm_blinking
    if not alarm_blinking:
        alarm_blinking = True
        alarm_blink()

def start_fut_blinking():
    global fut_blinking
    if not fut_blinking:
        fut_blinking = True
        fut_blink()

def stop_all_alarms():
    global alarm_blinking, fut_blinking
    alarm_blinking = False
    fut_blinking = False
    GPIO.output(RED_PIN, GPIO.LOW)
    GPIO.output(YELLOW_PIN, GPIO.LOW)
    root.config(background=default_background)
    stop_alarm_sound()

def alarm_blink():
    red_duration = 1000
    off_duration = 1000
    toggle_color_id = None

    def toggle_color():
        nonlocal toggle_color_id
        if global_alarm_active and alarm_blinking:
            current_color = root.cget("background")
            new_color = "red" if current_color != "red" else default_background
            root.config(background=new_color)
            GPIO.output(RED_PIN, GPIO.HIGH)  # LED를 계속 켜둠
            toggle_color_id = root.after(red_duration if new_color == "red" else off_duration, toggle_color)
        else:
            root.config(background=default_background)
            GPIO.output(RED_PIN, GPIO.LOW)
            if toggle_color_id:
                root.after_cancel(toggle_color_id)

    toggle_color()

def fut_blink():
    yellow_duration = 1000
    off_duration = 1000
    toggle_color_id = None

    def toggle_color():
        nonlocal toggle_color_id
        if global_fut_active and fut_blinking:
            current_color = root.cget("background")
            new_color = "yellow" if current_color != "yellow" else default_background
            root.config(background=new_color)
            GPIO.output(YELLOW_PIN, GPIO.HIGH)  # LED를 계속 켜둠
            toggle_color_id = root.after(yellow_duration if new_color == "yellow" else off_duration, toggle_color)
        else:
            root.config(background=default_background)
            GPIO.output(YELLOW_PIN, GPIO.LOW)
            if toggle_color_id:
                root.after_cancel(toggle_color_id)

    toggle_color()

def exit_fullscreen(event=None):
    utils.exit_fullscreen(root, event)

def enter_fullscreen(event=None):
    utils.enter_fullscreen(root, event)

def exit_application():
    utils.exit_application(root)

def update_system():
    utils.update_system(root)

def check_for_updates():
    utils.check_for_updates(root)

def show_update_notification(remote_commit):
    utils.show_update_notification(root, remote_commit)

def start_update(remote_commit):
    utils.start_update(root, remote_commit)

def ignore_update(remote_commit):
    utils.ignore_update(root, remote_commit)

def restart_application():
    utils.restart_application()

def get_system_info():
    try:
        current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()
    except subprocess.CalledProcessError:
        current_branch = "N/A"

    try:
        cpu_temp = os.popen("vcgencmd measure_temp").readline().replace("temp=", "").strip()
        cpu_usage = psutil.cpu_percent(interval=1)
        memory_usage = psutil.virtual_memory().percent
        disk_usage = psutil.disk_usage('/').percent
        net_io = psutil.net_io_counters()
        network_info = f"Sent: {net_io.bytes_sent / (1024 * 1024):.2f}MB, Recv: {net_io.bytes_recv / (1024 * 1024):.2f}MB"
        return f"IP: {get_ip_address()} | Branch: {current_branch} | Temp: {cpu_temp} | CPU: {cpu_usage}% | Mem: {memory_usage}% | Disk: {disk_usage}% | Net: {network_info}"
    except Exception as e:
        return f"System info could not be retrieved: {str(e)}"

def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        s.connect(('10.254.254.254', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = 'N/A'
    finally:
        s.close()
    return IP

def update_status_label():
    status_label.config(text=get_system_info())

def change_branch():
    global branch_window
    if branch_window and branch_window.winfo_exists():
        branch_window.focus()
        return

    branch_window = Toplevel(root)
    branch_window.title("브랜치 변경")
    branch_window.attributes("-topmost", True)

    try:
        current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()
        Label(branch_window, text=f"현재 브랜치: {current_branch}", font=("Arial", 12)).pack(pady=10)

        branches = subprocess.check_output(['git', 'branch', '-r']).decode().split('\n')
        branches = [branch.strip().replace('origin/', '') for branch in branches if branch]

        selected_branch = StringVar(branch_window)
        selected_branch.set(branches[0])
        ttk.Combobox(branch_window, textvariable=selected_branch, values=branches, font=("Arial", 12)).pack(pady=5)

        def switch_branch():
            new_branch = selected_branch.get()
            try:
                subprocess.check_output(['git', 'checkout', new_branch])
                messagebox.showinfo("브랜치 변경", f"{new_branch} 브랜치로 변경되었습니다.")
                branch_window.destroy()
                restart_application()
            except subprocess.CalledProcessError as e:
                messagebox.showerror("오류", f"브랜치 변경 중 오류 발생: {e}")

        Button(branch_window, text="브랜치 변경", command=switch_branch).pack(pady=10)
    except Exception as e:
        messagebox.showerror("오류", f"브랜치 정보를 가져오는 중 오류가 발생했습니다: {e}")
        branch_window.destroy()

def update_clock_thread(clock_label, date_label, stop_event):
    while not stop_event.is_set():
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M:%S")
        current_date = now.strftime("%Y-%m-%d %A")
        clock_label.config(text=current_time)
        date_label.config(text=current_date)
        time.sleep(1)

if __name__ == "__main__":
    root = tk.Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    default_background = root.cget("background")

    def signal_handler(sig, frame):
        print("Exiting gracefully...")
        GPIO.cleanup()  # GPIO 핀 초기화
        root.destroy()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    initialize_globals(root, change_branch)

    if not admin_password:
        prompt_new_password()

    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    root.bind("<Escape>", exit_fullscreen)

    settings = load_settings()
    modbus_boxes = settings.get("modbus_boxes", [])
    if isinstance(modbus_boxes, int):
        modbus_boxes = [None] * modbus_boxes

    analog_boxes = settings.get("analog_boxes", [])
    if isinstance(analog_boxes, int):
        analog_boxes = [None] * analog_boxes

    if not isinstance(modbus_boxes, list):
        raise TypeError("modbus_boxes should be a list, got {}".format(type(modbus_boxes)))

    if not isinstance(analog_boxes, list):
        raise TypeError("analog_boxes should be a list, got {}".format(type(analog_boxes)))

    main_frame = tk.Frame(root)
    main_frame.grid(row=0, column=0, sticky="nsew")

    # 클래스 인스턴스 생성 (프레임 배치 없음)
    modbus_ui = ModbusUI(main_frame, len(modbus_boxes), settings["modbus_gas_types"], lambda active, idx: set_alarm_status(active, f"modbus_{idx}"))
    analog_ui = AnalogUI(main_frame, len(analog_boxes), settings["analog_gas_types"], lambda active, idx: set_alarm_status(active, f"analog_{idx}"))

    ups_ui = None
    if settings.get("battery_box_enabled", 0):
        ups_ui = UPSMonitorUI(main_frame, 1)

    all_boxes = []

    # 클래스에서 프레임 수집
    if ups_ui:
        for i, frame in enumerate(ups_ui.box_frames):
            all_boxes.append((frame, f"ups_{i}"))

    for i, frame in enumerate(modbus_ui.box_frames):
        all_boxes.append((frame, f"modbus_{i}"))

    for i, frame in enumerate(analog_ui.box_frames):
        all_boxes.append((frame, f"analog_{i}"))

    # 각 상자의 알람 상태 초기화
    for _, idx in all_boxes:
        box_alarm_states[idx] = {'active': False, 'fut': False}

    # 최대 열의 수 설정
    max_columns = 6

    # 총 행의 수 계산
    num_rows = (len(all_boxes) + max_columns - 1) // max_columns

    # 그리드 행과 열의 가중치 설정
    main_frame.grid_rowconfigure(0, weight=1)  # 상단 여백
    main_frame.grid_rowconfigure(num_rows + 1, weight=1)  # 하단 여백
    main_frame.grid_columnconfigure(0, weight=1)  # 좌측 여백
    main_frame.grid_columnconfigure(max_columns + 1, weight=1)  # 우측 여백

    # 상자들이 위치하는 행과 열의 가중치를 0으로 설정
    for i in range(1, num_rows + 1):
        main_frame.grid_rowconfigure(i, weight=0)
    for i in range(1, max_columns + 1):
        main_frame.grid_columnconfigure(i, weight=0)

    # 상자 배치 시작 인덱스를 1로 변경
    row_index = 1
    column_index = 1

    # 프레임 배치
    for frame, idx in all_boxes:
        if column_index > max_columns:
            column_index = 1
            row_index += 1

        frame.grid(row=row_index, column=column_index, padx=2, pady=2)
        column_index += 1

    settings_button = tk.Button(root, text="⚙", command=lambda: prompt_new_password() if not admin_password else show_password_prompt(show_settings), font=("Arial", 20))

    def on_enter(event):
        event.widget.config(background="#b2b2b2", foreground="black")

    def on_leave(event):
        event.widget.config(background="#b2b2b2", foreground="black")

    settings_button.bind("<Enter>", on_enter)
    settings_button.bind("<Leave>", on_leave)

    settings_button.place(relx=1.0, rely=1.0, anchor='se')

    status_label = tk.Label(root, text="", font=("Arial", 10))
    status_label.place(relx=0.0, rely=1.0, anchor='sw')

    total_boxes = len(modbus_ui.box_frames) + len(analog_ui.box_frames) + (len(ups_ui.box_frames) if ups_ui else 0)

    if 0 <= total_boxes <= 6:
        clock_label = tk.Label(root, font=("Helvetica", 60, "bold"), fg="white", bg="black", anchor='center', padx=10, pady=10)
        clock_label.place(relx=0.5, rely=0.1, anchor='n')

        date_label = tk.Label(root, font=("Helvetica", 25), fg="white", bg="black", anchor='center', padx=5, pady=5)
        date_label.place(relx=0.5, rely=0.20, anchor='n')

        stop_event = threading.Event()
        clock_thread = threading.Thread(target=update_clock_thread, args=(clock_label, date_label, stop_event))
        clock_thread.start()

    def on_closing():
        if 0 <= total_boxes <= 4:
            stop_event.set()
            clock_thread.join()
        GPIO.cleanup()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    def system_info_thread():
        while True:
            update_status_label()
            time.sleep(1)

    if os.path.exists(utils.IGNORE_COMMIT_FILE):
        with open(utils.IGNORE_COMMIT_FILE, "r") as file:
            ignore_commit = file.read().strip().encode()
        utils.ignore_commit = ignore_commit

    utils.checking_updates = True
    threading.Thread(target=system_info_thread, daemon=True).start()
    threading.Thread(target=utils.check_for_updates, args=(root,), daemon=True).start()

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()



#settings.py


from tkinter import Toplevel, Label, Entry, Button, Frame, messagebox, StringVar, Checkbutton, IntVar
from tkinter import ttk
import json
import os
import sys
import threading
import subprocess
import time
import utils
# import pygame  # 오디오 재생을 위한 pygame 모듈 추가

SETTINGS_FILE = "settings.json"

# 글로벌 변수 선언
settings_window = None
password_window = None
attempt_count = 0
lock_time = 0
lock_window = None
box_settings_window = None
new_password_window = None
update_notification_frame = None
ignore_commit = None
branch_window = None
audio_selection_window = None  # 오디오 선택 창 전역 변수
root = None
selected_audio_file = None

def initialize_globals(main_root, change_branch_func):
    global root, change_branch
    root = main_root
    change_branch = change_branch_func

# 암호화 키 생성 및 로드
key = utils.load_key()
cipher_suite = utils.cipher_suite

def encrypt_data(data):
    return utils.encrypt_data(data)

def decrypt_data(data):
    return utils.decrypt_data(data)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'rb') as file:
            encrypted_data = file.read()
        decrypted_data = decrypt_data(encrypted_data)
        return json.loads(decrypted_data)
    else:
        return {
            "modbus_boxes": 0,  # 기본값으로 0으로 초기화
            "analog_boxes": 0,  # 기본값으로 0으로 초기화
            "admin_password": None,
            "modbus_gas_types": {},
            "analog_gas_types": {},
            "audio_file": None,  # 오디오 파일 설정 추가
            "battery_box_enabled": 0  # 배터리 박스 활성화 설정 추가
        }

def save_settings(settings):
    with open(SETTINGS_FILE, 'wb') as file:
        encrypted_data = encrypt_data(json.dumps(settings))
        file.write(encrypted_data)

settings = load_settings()
admin_password = settings.get("admin_password")
selected_audio_file = settings.get("audio_file")

def prompt_new_password():
    global new_password_window
    if new_password_window and new_password_window.winfo_exists():
        new_password_window.focus()
        return

    new_password_window = Toplevel(root)
    new_password_window.title("관리자 비밀번호 설정")
    new_password_window.attributes("-topmost", True)

    Label(new_password_window, text="새로운 관리자 비밀번호를 입력하세요", font=("Arial", 12)).pack(pady=10)
    new_password_entry = Entry(new_password_window, show="*", font=("Arial", 12))
    new_password_entry.pack(pady=5)
    utils.create_keypad(new_password_entry, new_password_window, geometry="pack")

    def confirm_password():
        new_password = new_password_entry.get()
        new_password_window.destroy()
        prompt_confirm_password(new_password)

    Button(new_password_window, text="다음", command=confirm_password).pack(pady=5)

def prompt_confirm_password(new_password):
    global new_password_window
    if new_password_window and new_password_window.winfo_exists():
        new_password_window.focus()
        return

    new_password_window = Toplevel(root)
    new_password_window.title("비밀번호 확인")
    new_password_window.attributes("-topmost", True)

    Label(new_password_window, text="비밀번호를 다시 입력하세요", font=("Arial", 12)).pack(pady=10)
    confirm_password_entry = Entry(new_password_window, show="*", font=("Arial", 12))
    confirm_password_entry.pack(pady=5)
    utils.create_keypad(confirm_password_entry, new_password_window, geometry="pack")

    def save_new_password():
        confirm_password = confirm_password_entry.get()
        if new_password == confirm_password and new_password:
            settings["admin_password"] = new_password
            save_settings(settings)
            messagebox.showinfo("비밀번호 설정", "새로운 비밀번호가 설정되었습니다.")
            new_password_window.destroy()
            utils.restart_application()
        else:
            messagebox.showerror("비밀번호 오류", "비밀번호가 일치하지 않거나 유효하지 않습니다.")
            new_password_window.destroy()
            prompt_new_password()

    Button(new_password_window, text="저장", command=save_new_password).pack(pady=5)

def show_password_prompt(callback):
    global attempt_count, lock_time, password_window, settings_window, lock_window

    if time.time() < lock_time:
        if not lock_window or not lock_window.winfo_exists():
            lock_window = Toplevel(root)
            lock_window.title("잠금")
            lock_window.attributes("-topmost", True)
            lock_window.geometry("300x150")
            lock_label = Label(lock_window, text="", font=("Arial", 12))
            lock_label.pack(pady=10)
            Button(lock_window, text="확인", command=lock_window.destroy).pack(pady=5)

            def update_lock_message():
                if lock_label.winfo_exists():
                    remaining_time = int(lock_time - time.time())
                    lock_label.config(text=f"비밀번호 입력 시도가 5회 초과되었습니다.\n{remaining_time}초 후에 다시 시도하십시오.")
                    if remaining_time > 0:
                        root.after(1000, update_lock_message)
                    else:
                        lock_window.destroy()

            update_lock_message()
        return

    if password_window and password_window.winfo_exists():
        password_window.focus()
        return

    if settings_window and settings_window.winfo_exists():
        settings_window.destroy()

    password_window = Toplevel(root)
    password_window.title("비밀번호 입력")
    password_window.attributes("-topmost", True)

    Label(password_window, text="비밀번호를 입력하세요", font=("Arial", 12)).pack(pady=10)
    password_entry = Entry(password_window, show="*", font=("Arial", 12))
    password_entry.pack(pady=5)
    utils.create_keypad(password_entry, password_window, geometry="pack")

    def check_password():
        global attempt_count, lock_time
        if password_entry.get() == admin_password:
            password_window.destroy()
            callback()
        else:
            attempt_count += 1
            if attempt_count >= 5:
                lock_time = time.time() + 60  # 60초 잠금
                attempt_count = 0
                password_window.destroy()
                show_password_prompt(callback)
            else:
                Label(password_window, text="비밀번호가 틀렸습니다.", font=("Arial", 12), fg="red").pack(pady=5)

    Button(password_window, text="확인", command=check_password).pack(pady=5)

def show_settings():
    global settings_window, selected_audio_file
    if settings_window and settings_window.winfo_exists():
        settings_window.focus()
        return

    settings_window = Toplevel(root)
    settings_window.title("설정 메뉴")
    settings_window.attributes("-topmost", True)

    Label(settings_window, text="GMS-1000 설정", font=("Arial", 16)).pack(pady=10)

    button_font = ("Arial", 14)
    button_style = {'font': button_font, 'width': 25, 'height': 2, 'padx': 10, 'pady': 10}

    Button(settings_window, text="상자 설정", command=show_box_settings, **button_style).pack(pady=5)
    Button(settings_window, text="비밀번호 변경", command=prompt_new_password, **button_style).pack(pady=5)

    # "전체 화면 설정"과 "창 크기 설정" 버튼을 한 줄에 나란히 배치
    frame1 = Frame(settings_window)
    frame1.pack(pady=5)
    fullscreen_button = Button(frame1, text="전체 화면 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: utils.enter_fullscreen(root))
    fullscreen_button.grid(row=0, column=0)
    windowed_button = Button(frame1, text="창 크기 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: utils.exit_fullscreen(root))
    windowed_button.grid(row=0, column=1)

    # "시스템 업데이트"와 "브랜치 변경" 버튼을 한 줄에 나란히 배치
    frame2 = Frame(settings_window)
    frame2.pack(pady=5)
    update_button = Button(frame2, text="시스템 업데이트", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: threading.Thread(target=check_and_update_system).start())
    update_button.grid(row=0, column=0)
    branch_button = Button(frame2, text="브랜치 변경", font=button_font, width=12, height=2, padx=10, pady=10, command=change_branch)
    branch_button.grid(row=0, column=1)

    # "재시작" 및 "종료" 버튼을 추가하고 동일한 위치에 배치
    frame3 = Frame(settings_window)
    frame3.pack(pady=5)
    restart_button = Button(frame3, text="재시작", font=button_font, width=12, height=2, padx=10, pady=10, command=utils.restart_application)
    restart_button.grid(row=0, column=0)
    exit_button = Button(frame3, text="종료", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: utils.exit_application(root))
    exit_button.grid(row=0, column=1)

    # 현재 선택된 오디오 파일을 표시
    selected_audio_label = Label(settings_window, text=f"선택된 오디오 파일: {os.path.basename(selected_audio_file) if selected_audio_file else 'None'}", font=("Arial", 12))
    selected_audio_label.pack(pady=10)

    # 오디오 파일 선택 버튼 추가
    def select_audio_file():
        global selected_audio_file, audio_selection_window

        if audio_selection_window and audio_selection_window.winfo_exists():
            audio_selection_window.focus()
            return

        audio_folder = "audio"
        audio_files = [f for f in os.listdir(audio_folder) if f.endswith(('.mp3', '.wav'))]
        if not audio_files:
            messagebox.showerror("오류", "audio 폴더에 mp3 또는 wav 파일이 없습니다.")
            return

        def on_audio_select(event):
            global selected_audio_file
            selected_audio_file = os.path.join(audio_folder, audio_combo.get())
            settings["audio_file"] = selected_audio_file
            save_settings(settings)
            selected_audio_label.config(text=f"선택된 오디오 파일: {os.path.basename(selected_audio_file)}")
            messagebox.showinfo("오디오 파일 선택", f"선택된 오디오 파일: {selected_audio_file}")
            audio_selection_window.destroy()

        audio_selection_window = Toplevel(settings_window)
        audio_selection_window.title("오디오 파일 선택")
        audio_selection_window.attributes("-topmost", True)
        Label(audio_selection_window, text="오디오 파일을 선택하세요", font=("Arial", 12)).pack(pady=10)
        audio_combo = ttk.Combobox(audio_selection_window, values=audio_files, font=("Arial", 12))
        audio_combo.pack(pady=5)
        audio_combo.bind("<<ComboboxSelected>>", on_audio_select)

    Button(settings_window, text="경고 오디오 선택", command=select_audio_file, font=("Arial", 14), width=25, height=2, padx=10, pady=10).pack(pady=5)

def check_and_update_system():
    try:
        current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()
        local_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
        remote_commit = subprocess.check_output(['git', 'ls-remote', 'origin', current_branch]).split()[0]

        if local_commit != remote_commit:
            messagebox.showinfo("시스템 업데이트", "새로운 업데이트가 있습니다. 시스템을 업데이트합니다.")
            utils.update_system(root)
        else:
            messagebox.showinfo("시스템 업데이트", "현재 최신 버전을 사용 중입니다.")
    except Exception as e:
        messagebox.showerror("시스템 업데이트 오류", f"업데이트 확인 중 오류가 발생했습니다: {e}")

def show_box_settings():
    global box_settings_window
    if box_settings_window and box_settings_window.winfo_exists():
        box_settings_window.attributes("-topmost", True)  # 창이 존재하는 경우 다시 최상위로 설정
        box_settings_window.focus()
        return

    box_settings_window = Toplevel(root)
    box_settings_window.title("상자 설정")
    box_settings_window.attributes("-topmost", True)

    # 상자 수와 배터리 박스를 동일 줄에 배치
    Label(box_settings_window, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(row=0, column=0, padx=2, pady=2, sticky="w")
    modbus_boxes_var = StringVar(value=str(settings.get("modbus_boxes", 0)))
    analog_boxes_var = StringVar(value=str(settings.get("analog_boxes", 0)))

    # 상자 수 변경 시 자동으로 update_gas_type_options 호출
    modbus_boxes_var.trace_add("write", lambda *args: update_gas_type_options())
    analog_boxes_var.trace_add("write", lambda *args: update_gas_type_options())

    # 배터리 박스 활성화 체크박스 추가
    battery_box_var = IntVar(value=settings.get("battery_box_enabled", 0))
    battery_box_check = Checkbutton(box_settings_window, text="배터리 박스 활성화", variable=battery_box_var, font=("Arial", 12))
    battery_box_check.grid(row=0, column=2, padx=2, pady=2, sticky="e")  # 같은 줄 오른쪽 끝에 배치

    # 배터리 박스 변경 시 총합 검사 함수 호출
    battery_box_var.trace_add("write", lambda *args: check_total_boxes())

    try:
        modbus_box_count = int(modbus_boxes_var.get())
        analog_box_count = int(analog_boxes_var.get())
    except ValueError:
        modbus_box_count = 0
        analog_box_count = 0
        modbus_boxes_var.set("0")
        analog_boxes_var.set("0")
        messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    def modify_box_count(var, delta):
        current_value = int(var.get())
        other_value = int(analog_boxes_var.get() if var == modbus_boxes_var else modbus_boxes_var.get())
        battery_box = battery_box_var.get()
        new_value = current_value + delta
        total_boxes = new_value + other_value + battery_box
        if 0 <= new_value <= 12 and total_boxes <= 12:
            var.set(str(new_value))
        else:
            messagebox.showerror("입력 오류", "상자의 총합이 12개를 초과할 수 없습니다.")

    frame_modbus = Frame(box_settings_window)
    frame_modbus.grid(row=0, column=1, padx=2, pady=2)
    Button(frame_modbus, text="-", command=lambda: modify_box_count(modbus_boxes_var, -1), font=("Arial", 12)).grid(row=0, column=0, padx=2, pady=2)
    Label(frame_modbus, textvariable=modbus_boxes_var, font=("Arial", 12)).grid(row=0, column=1, padx=2, pady=2)
    Button(frame_modbus, text="+", command=lambda: modify_box_count(modbus_boxes_var, 1), font=("Arial", 12)).grid(row=0, column=2, padx=2, pady=2)

    Label(box_settings_window, text="4~20mA 상자 수", font=("Arial", 12)).grid(row=1, column=0, padx=2, pady=2)
    frame_analog = Frame(box_settings_window)
    frame_analog.grid(row=1, column=1, padx=2, pady=2)
    Button(frame_analog, text="-", command=lambda: modify_box_count(analog_boxes_var, -1), font=("Arial", 12)).grid(row=0, column=0, padx=2, pady=2)
    Label(frame_analog, textvariable=analog_boxes_var, font=("Arial", 12)).grid(row=0, column=1, padx=2, pady=2)
    Button(frame_analog, text="+", command=lambda: modify_box_count(analog_boxes_var, 1), font=("Arial", 12)).grid(row=0, column=2, padx=2, pady=2)

    gas_type_labels = ["ORG", "ARF-T", "HMDS", "HC-100"]
    modbus_gas_type_vars = []
    analog_gas_type_vars = []
    modbus_gas_type_combos = []
    analog_gas_type_combos = []
    modbus_labels = []
    analog_labels = []

    def update_gas_type_options():
        for label in modbus_labels:
            label.grid_remove()
        for combo in modbus_gas_type_combos:
            combo.grid_remove()
        for label in analog_labels:
            label.grid_remove()
        for combo in analog_gas_type_combos:
            combo.grid_remove()

        modbus_boxes = int(modbus_boxes_var.get())
        analog_boxes = int(analog_boxes_var.get())

        for i in range(modbus_boxes):  # Modbus 상자 설정을 표시
            if len(modbus_gas_type_combos) <= i:
                modbus_gas_type_var = StringVar(value=settings["modbus_gas_types"].get(f"modbus_box_{i}", "ORG"))
                modbus_gas_type_vars.append(modbus_gas_type_var)
                combo = ttk.Combobox(box_settings_window, textvariable=modbus_gas_type_var, values=gas_type_labels, font=("Arial", 12))
                modbus_gas_type_combos.append(combo)
                label = Label(box_settings_window, text=f"Modbus 상자 {i + 1} 유형", font=("Arial", 12))
                modbus_labels.append(label)
            else:
                combo = modbus_gas_type_combos[i]
                label = modbus_labels[i]

            label.grid(row=i + 2, column=0, padx=2, pady=2)
            combo.grid(row=i + 2, column=1, padx=2, pady=2)

        for i in range(analog_boxes):  # 4~20mA 상자 설정을 표시
            if len(analog_gas_type_combos) <= i:
                analog_gas_type_var = StringVar(value=settings["analog_gas_types"].get(f"analog_box_{i}", "ORG"))
                analog_gas_type_vars.append(analog_gas_type_var)
                combo = ttk.Combobox(box_settings_window, textvariable=analog_gas_type_var, values=gas_type_labels, font=("Arial", 12))
                analog_gas_type_combos.append(combo)
                label = Label(box_settings_window, text=f"4~20mA 상자 {i + 1} 유형", font=("Arial", 12))
                analog_labels.append(label)
            else:
                combo = analog_gas_type_combos[i]
                label = analog_labels[i]

            label.grid(row=i + 2, column=2, padx=2, pady=2)
            combo.grid(row=i + 2, column=3, padx=2, pady=2)

    update_gas_type_options()

    # 총합 검사 함수
    def check_total_boxes():
        modbus_boxes = int(modbus_boxes_var.get())
        analog_boxes = int(analog_boxes_var.get())
        battery_box = battery_box_var.get()
        total_boxes = modbus_boxes + analog_boxes + battery_box
        if total_boxes > 12:
            # 배터리 박스 체크박스 변경으로 인한 초과 시 체크 해제
            if battery_box_var.get() == 1:
                battery_box_var.set(0)
            messagebox.showerror("입력 오류", "상자의 총합이 12개를 초과할 수 없습니다.")

    def save_and_close():
        try:
            modbus_boxes = int(modbus_boxes_var.get())
            analog_boxes = int(analog_boxes_var.get())
            battery_box = battery_box_var.get()
            if modbus_boxes + analog_boxes + battery_box > 12:
                messagebox.showerror("입력 오류", "상자의 총합이 12개를 초과할 수 없습니다.")
                return
            settings["modbus_boxes"] = modbus_boxes
            settings["analog_boxes"] = analog_boxes
            settings["battery_box_enabled"] = battery_box  # 배터리 박스 활성화 설정 저장
            for i, var in enumerate(modbus_gas_type_vars):
                settings["modbus_gas_types"][f"modbus_box_{i}"] = var.get()
            for i, var in enumerate(analog_gas_type_vars):
                settings["analog_gas_types"][f"analog_box_{i}"] = var.get()
            save_settings(settings)
            messagebox.showinfo("설정 저장", "설정이 저장되었습니다.")
            box_settings_window.destroy()
            utils.restart_application()  # 설정이 변경되면 애플리케이션을 재시작
        except ValueError:
            messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=16, columnspan=4, pady=10)
