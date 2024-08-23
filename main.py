import json
import os
import time
from tkinter import Tk, Frame, Button, Label, Entry, messagebox, StringVar, Toplevel
from tkinter import ttk
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
import threading
import psutil
import signal
import sys
import subprocess
import socket
from settings import show_settings, prompt_new_password, show_password_prompt, load_settings, save_settings, initialize_globals
import utils  # utils 모듈 임포트 추가
import tkinter as tk
import pygame  # 오디오 재생을 위한 pygame 모듈 추가
import queue  # 큐 사용을 위해 추가

# 설정 값을 저장할 파일 경로
SETTINGS_FILE = "settings.json"

# 암호화 키 생성 및 로드
key = utils.load_key()
cipher_suite = utils.cipher_suite

def encrypt_data(data):
    return utils.encrypt_data(data)

def decrypt_data(data):
    return utils.decrypt_data(data)

settings = load_settings()  # 여기서 settings를 불러옵니다
admin_password = settings.get("admin_password")  # settings를 불러온 후에 admin_password를 설정합니다

ignore_commit = None  # ignore_commit 변수를 전역 변수로 선언하고 초기화
update_notification_frame = None  # update_notification_frame 변수를 전역 변수로 선언하고 초기화
checking_updates = True  # 전역 변수로 선언 및 초기화
branch_window = None  # branch_window 변수를 전역 변수로 선언 및 초기화
alarm_active = False  # 알람 상태를 저장하는 전역 변수
alarm_blinking = False  # 알람 깜빡임 상태를 저장하는 전역 변수
selected_audio_file = settings.get("audio_file")  # 오디오 파일 경로를 settings에서 불러옴
audio_playing = False  # 오디오 재생 상태를 저장하는 변수

# 오디오 재생 큐와 락 초기화
audio_queue = queue.Queue()
audio_lock = threading.Lock()

# 현재 알람을 제어하는 상자 ID (None일 경우 알람을 제어하는 상자가 없음)
current_alarm_box_id = None

# 오디오 재생 초기화
pygame.mixer.init()

def play_alarm_sound(box_id):
    global selected_audio_file, audio_playing, current_alarm_box_id
    with audio_lock:
        if current_alarm_box_id is None or current_alarm_box_id == box_id:
            current_alarm_box_id = box_id
            if not audio_playing:
                audio_queue.put(selected_audio_file)
                if not pygame.mixer.music.get_busy():
                    play_next_in_queue()

def play_next_in_queue():
    global audio_playing, current_alarm_box_id
    if not audio_queue.empty():
        next_audio_file = audio_queue.get()
        pygame.mixer.music.load(next_audio_file)
        pygame.mixer.music.play()
        audio_playing = True
    else:
        current_alarm_box_id = None

def check_music_end():
    global audio_playing
    if not pygame.mixer.music.get_busy():
        audio_playing = False
        play_next_in_queue()  # 큐에 남은 소리가 있으면 재생
    root.after(100, check_music_end)

def stop_alarm_sound(box_id):
    global audio_playing, current_alarm_box_id
    with audio_lock:
        if current_alarm_box_id == box_id:
            pygame.mixer.music.stop()
            audio_playing = False
            while not audio_queue.empty():  # 큐를 비움
                audio_queue.get()
            current_alarm_box_id = None

def set_alarm_status(active, box_id):
    global alarm_active, alarm_blinking
    alarm_active = active
    if alarm_active and not alarm_blinking:
        alarm_blinking = True
        alarm_blink()
        play_alarm_sound(box_id)  # 알람 소리 재생
    elif not alarm_active and alarm_blinking:
        alarm_blinking = False
        root.config(background=default_background)
        stop_alarm_sound(box_id)  # 알람 소리 정지

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

def alarm_blink():
    red_duration = 200  # 빨간색 상태에서 머무는 시간 (밀리초)
    off_duration = 200  # 기본 배경색 상태에서 머무는 시간 (밀리초)

    def toggle_color():
        if alarm_active:
            current_color = root.cget("background")
            new_color = "red" if current_color != "red" else default_background
            root.config(background=new_color)
            root.after(red_duration if new_color == "red" else off_duration, toggle_color)
        else:
            root.config(background=default_background)
            root.after_cancel(toggle_color)

    toggle_color()

if __name__ == "__main__":
    root = tk.Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    default_background = root.cget("background")

    def signal_handler(sig, frame):
        print("Exiting gracefully...")
        root.destroy()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    initialize_globals(root, change_branch)  # change_branch 함수 전달

    if not admin_password:
        prompt_new_password()

    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    root.bind("<Escape>", exit_fullscreen)

    # main.py 내의 코드를 수정하여 정수 값을 리스트로 변환
    modbus_boxes = settings.get("modbus_boxes", [])
    if isinstance(modbus_boxes, int):
        modbus_boxes = [None] * modbus_boxes  # 정수 값을 리스트로 변환

    analog_boxes = settings.get("analog_boxes", [])
    if isinstance(analog_boxes, int):
        analog_boxes = [None] * analog_boxes  # 정수 값을 리스트로 변환

    # modbus_boxes와 analog_boxes가 리스트인지 확인
    if not isinstance(modbus_boxes, list):
        raise TypeError("modbus_boxes should be a list, got {}".format(type(modbus_boxes)))

    if not isinstance(analog_boxes, list):
        raise TypeError("analog_boxes should be a list, got {}".format(type(analog_boxes)))

    main_frame = tk.Frame(root)
    main_frame.grid(row=0, column=0)

    # main.py 내에서 modbus_ui 초기화 부분 수정
    modbus_ui = ModbusUI(main_frame, len(modbus_boxes), settings["modbus_gas_types"], set_alarm_status)
    analog_ui = AnalogUI(main_frame, len(analog_boxes), settings["analog_gas_types"], set_alarm_status)

    # 그리드 레이아웃의 행과 열 가중치 설정
    max_columns = 6  # 한 줄에 최대 6개 상자 배치
    row_index = 0
    column_index = 0

    for i in range(max_columns):
        root.grid_columnconfigure(i, weight=1)

    for i in range((len(modbus_boxes) + len(analog_boxes)) // max_columns + 1):
        root.grid_rowconfigure(i, weight=1)

    # 모드버스 상자들을 먼저 배치
    for i in range(len(modbus_boxes)):
        if column_index >= max_columns:
            column_index = 0
            row_index += 1

        modbus_ui.box_frame.grid(row=row_index, column=column_index, padx=10, pady=10, sticky="nsew")
        column_index += 1

    # 아날로그 상자들을 계속해서 배치
    for i in range(len(analog_boxes)):
        if column_index >= max_columns:
            column_index = 0
            row_index += 1

        analog_ui.box_frame.grid(row=row_index, column=column_index, padx=10, pady=10, sticky="nsew")
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

    def system_info_thread():
        while True:
            update_status_label()
            time.sleep(1)

    # 기록된 ignore_commit을 로드
    if os.path.exists(utils.IGNORE_COMMIT_FILE):
        with open(utils.IGNORE_COMMIT_FILE, "r") as file:
            ignore_commit = file.read().strip().encode()
        utils.ignore_commit = ignore_commit

    utils.checking_updates = True
    threading.Thread(target=system_info_thread, daemon=True).start()
    threading.Thread(target=utils.check_for_updates, args=(root,), daemon=True).start()

    check_music_end()  # 음악 재생 상태 확인 함수 호출

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
