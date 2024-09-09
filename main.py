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
import queue
import datetime
import locale
import RPi.GPIO as GPIO  # GPIO 제어를 위한 라이브러리 추가

locale.setlocale(locale.LC_TIME, 'ko_KR.UTF-8')

SETTINGS_FILE = "settings.json"

key = utils.load_key()
cipher_suite = utils.cipher_suite

# GPIO 설정
GPIO.setmode(GPIO.BCM)  # BCM 모드로 설정
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
alarm_active = False
fut_active = False
alarm_blinking = False
fut_blinking = False
selected_audio_file = settings.get("audio_file")
audio_playing = False

audio_queue = queue.Queue()
audio_lock = threading.Lock()

current_alarm_box_id = None
last_signal_type = None
signal_received_time = 0

pygame.mixer.init()

def play_alarm_sound(box_id):
    global selected_audio_file, audio_playing, current_alarm_box_id
    if selected_audio_file is None:
        print("No audio file selected. Skipping alarm sound.")
        return

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
        print(f"Trying to play: {next_audio_file}")

        if next_audio_file is None:
            print("Error: No audio file to play. Skipping.")
            current_alarm_box_id = None
            return

        if os.path.isfile(next_audio_file):
            try:
                pygame.mixer.music.load(next_audio_file)
                pygame.mixer.music.play()
                audio_playing = True
            except pygame.error as e:
                print(f"Pygame error: {e}")
                current_alarm_box_id = None
        else:
            print(f"File not found: {next_audio_file}")
            current_alarm_box_id = None
    else:
        current_alarm_box_id = None

def check_music_end():
    global audio_playing
    if not pygame.mixer.music.get_busy():
        audio_playing = False
        play_next_in_queue()
    root.after(100, check_music_end)

def stop_alarm_sound(box_id):
    global audio_playing, current_alarm_box_id
    with audio_lock:
        if current_alarm_box_id == box_id:
            pygame.mixer.music.stop()
            audio_playing = False
            while not audio_queue.empty():
                audio_queue.get()
            current_alarm_box_id = None  # 알람이 멈추면 현재 알람 ID 초기화

def set_alarm_status(active, box_id, fut=False):
    global alarm_active, fut_active, last_signal_type, signal_received_time, current_alarm_box_id

    # 기존 알람이 이미 활성화된 경우 새 요청 무시
    if current_alarm_box_id is not None and current_alarm_box_id != box_id:
        print(f"Alarm from box {box_id} ignored because box {current_alarm_box_id} is already active.")
        return

    current_time = time.time()

    # 신호가 기존 신호와 다르거나 1초 이상 경과했을 때만 처리
    if (last_signal_type != (active, fut)) or (current_time - signal_received_time > 1):
        alarm_active = active
        fut_active = fut
        last_signal_type = (active, fut)
        signal_received_time = current_time
        current_alarm_box_id = box_id

        # 알람을 별도 스레드에서 처리하여 메인 스레드를 차단하지 않도록 함
        threading.Thread(target=handle_alarm, args=(box_id,)).start()

def handle_alarm(box_id):
    global alarm_active, fut_active

    if fut_active:
        stop_alarm_sound(box_id)
        alarm_blinking = False
        fut_blinking = True
        fut_blink()
    elif alarm_active:
        fut_blinking = False
        alarm_blinking = True
        alarm_blink()
        play_alarm_sound(box_id)
    else:
        fut_blinking = False
        alarm_blinking = False
        GPIO.output(RED_PIN, GPIO.LOW)  # LED 끄기
        GPIO.output(YELLOW_PIN, GPIO.LOW)  # LED 끄기
        root.config(background=default_background)
        stop_alarm_sound(box_id)

def alarm_blink():
    red_duration = 1000
    off_duration = 1000
    toggle_color_id = None

    def toggle_color():
        nonlocal toggle_color_id
        if alarm_active:
            current_color = root.cget("background")
            new_color = "red" if current_color != "red" else default_background
            root.config(background=new_color)
            GPIO.output(RED_PIN, GPIO.HIGH)  # 빨강 LED 켜기
            toggle_color_id = root.after(red_duration if new_color == "red" else off_duration, toggle_color)
        else:
            root.config(background=default_background)
            GPIO.output(RED_PIN, GPIO.LOW)  # LED 끄기
            if toggle_color_id:
                root.after_cancel(toggle_color_id)

    toggle_color()

def fut_blink():
    yellow_duration = 1000
    off_duration = 1000
    toggle_color_id = None

    def toggle_color():
        nonlocal toggle_color_id
        if fut_active:
            current_color = root.cget("background")
            new_color = "yellow" if current_color != "yellow" else default_background
            root.config(background=new_color)
            GPIO.output(YELLOW_PIN, GPIO.HIGH)  # 노랑 LED 켜기
            toggle_color_id = root.after(yellow_duration if new_color == "yellow" else off_duration, toggle_color)
        else:
            root.config(background=default_background)
            GPIO.output(YELLOW_PIN, GPIO.LOW)  # LED 끄기
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
    main_frame.grid(row=0, column=0)

    modbus_ui = ModbusUI(main_frame, len(modbus_boxes), settings["modbus_gas_types"], set_alarm_status)
    analog_ui = AnalogUI(main_frame, len(analog_boxes), settings["analog_gas_types"], set_alarm_status)

    ups_ui = None
    if settings.get("battery_box_enabled", 0):
        ups_ui = UPSMonitorUI(main_frame, 1)

    all_boxes = []

    if ups_ui:
        all_boxes.append((ups_ui, 0))

    for i in range(len(modbus_boxes)):
        all_boxes.append((modbus_ui, i))

    for i in range(len(analog_boxes)):
        all_boxes.append((analog_ui, i))

    row_index = 0
    column_index = 0
    max_columns = 6

    for ui, idx in all_boxes:
        if column_index >= max_columns:
            column_index = 0
            row_index += 1

        if isinstance(ui, (ModbusUI, AnalogUI, UPSMonitorUI)):
            ui.box_frame.grid(row=row_index, column=column_index, padx=10, pady=10, sticky="nsew")

        column_index += 1

    for i in range(max_columns):
        main_frame.grid_columnconfigure(i, weight=1)

    for i in range((len(all_boxes) + max_columns - 1) // max_columns):
        main_frame.grid_rowconfigure(i, weight=1)

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

    total_boxes = len(modbus_boxes) + len(analog_boxes) + (1 if ups_ui else 0)

    if 0 <= total_boxes <= 4:
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
        GPIO.cleanup()  # GPIO 핀 초기화
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

    check_music_end()

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
