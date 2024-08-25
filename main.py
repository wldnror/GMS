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
import utils
import tkinter as tk
import pygame
import queue

# 설정 값을 저장할 파일 경로
SETTINGS_FILE = "settings.json"

# 암호화 키 생성 및 로드
key = utils.load_key()
cipher_suite = utils.cipher_suite

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
alarm_blinking = False
selected_audio_file = settings.get("audio_file")
audio_playing = False

audio_queue = queue.Queue()
audio_lock = threading.Lock()

current_alarm_box_id = None

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
            current_alarm_box_id = None

def set_alarm_status(active, box_id):
    global alarm_active, alarm_blinking
    alarm_active = active
    if alarm_active and not alarm_blinking:
        alarm_blinking = True
        alarm_blink()
        play_alarm_sound(box_id)
    elif not alarm_active and alarm_blinking:
        alarm_blinking = False
        root.config(background=default_background)
        stop_alarm_sound(box_id)

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
    red_duration = 200
    off_duration = 200

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

    # 디지털 및 아날로그 상자를 순차적으로 배치합니다.
    row_index = 0
    column_index = 0
    max_columns = 6

    for i in range(max(len(modbus_boxes), len(analog_boxes))):
        if i < len(modbus_boxes):
            box_frame = modbus_ui.box_frames[i][0]
            box_frame.grid(row=row_index, column=column_index, padx=5, pady=5)
            column_index += 1

        if i < len(analog_boxes):
            if column_index >= max_columns:
                column_index = 0
                row_index += 1
            box_frame = analog_ui.box_frames[i][0]
            box_frame.grid(row=row_index, column=column_index, padx=5, pady=5)
            column_index += 1

        if column_index >= max_columns:
            column_index = 0
            row_index += 1

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
