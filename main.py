import json
import os
import time
from tkinter import Tk, Frame, Button, Label, Entry, messagebox, StringVar, Toplevel, Canvas
from tkinter import ttk
import random
import threading
import queue
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
import psutil
import signal
import sys
import subprocess
import socket
from cryptography.fernet import Fernet
from settings import show_settings, prompt_new_password, show_password_prompt, load_settings, save_settings, initialize_globals

# 설정 값을 저장할 파일 경로
SETTINGS_FILE = "settings.json"
KEY_FILE = "secret.key"
IGNORE_COMMIT_FILE = "ignore_commit.txt"

# 암호화 키 생성 및 로드
def generate_key():
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as key_file:
        key_file.write(key)

def load_key():
    if not os.path.exists(KEY_FILE):
        generate_key()
    with open(KEY_FILE, "rb") as key_file:
        return key_file.read()

key = load_key()
cipher_suite = Fernet(key)

def encrypt_data(data):
    return cipher_suite.encrypt(data.encode())

def decrypt_data(data):
    return cipher_suite.decrypt(data).decode()

settings = load_settings()
admin_password = settings.get("admin_password")

def create_keypad(entry, parent, row=None, column=None, columnspan=1, geometry="grid"):
    keypad_frame = Frame(parent)
    if geometry == "grid":
        keypad_frame.grid(row=row, column=column, columnspan=columnspan, pady=5)
    elif geometry == "pack":
        keypad_frame.pack()

    def on_button_click(char):
        if char == 'DEL':
            current_text = entry.get()
            entry.delete(0, 'end')
            entry.insert(0, current_text[:-1])
        elif char == 'CLR':
            entry.delete(0, 'end')
        else:
            entry.insert('end', char)

    buttons = [str(i) for i in range(10)]
    random.shuffle(buttons)
    buttons.append('CLR')
    buttons.append('DEL')

    rows = 4
    cols = 3
    for i, button in enumerate(buttons):
        b = Button(keypad_frame, text=button, width=5, height=2, command=lambda b=button: on_button_click(b))
        b.grid(row=i // cols, column=i % cols, padx=5, pady=5)

    return keypad_frame

def exit_fullscreen(event=None):
    root.attributes("-fullscreen", False)
    root.attributes("-topmost", False)

def enter_fullscreen(event=None):
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

def exit_application():
    root.destroy()
    sys.exit(0)

def update_system():
    global checking_updates
    checking_updates = False  # 업데이트 중에 확인을 중지
    try:
        result = subprocess.run(['git', 'pull'], capture_output=True, text=True)
        message = "업데이트 완료. 애플리케이션을 재시작합니다."
        root.after(2000, restart_application)
    except Exception as e:
        message = f"업데이트 중 오류 발생: {e}"
    
    messagebox.showinfo("시스템 업데이트", message)

def check_for_updates():
    global ignore_commit
    while checking_updates:
        try:
            current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()
            local_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
            remote_commit = subprocess.check_output(['git', 'ls-remote', 'origin', current_branch]).split()[0]
            
            if local_commit != remote_commit and remote_commit != ignore_commit:
                show_update_notification(remote_commit)
        except Exception as e:
            print(f"Error checking for updates: {e}")
        
        time.sleep(1)

def show_update_notification(remote_commit):
    global update_notification_frame
    if update_notification_frame and update_notification_frame.winfo_exists():
        return

    def on_yes():
        start_update(remote_commit)
    def on_no():
        ignore_update(remote_commit)

    update_notification_frame = Frame(root)
    update_notification_frame.place(relx=0.5, rely=0.95, anchor='center')

    update_label = Label(update_notification_frame, text="새로운 버젼이 있습니다. 업데이트를 진행하시겠습니까?", font=("Arial", 15), fg="red")
    update_label.pack(side="left", padx=5)

    yes_button = Button(update_notification_frame, text="예", command=on_yes, font=("Arial", 14), fg="red")
    yes_button.pack(side="left", padx=5)
    
    no_button = Button(update_notification_frame, text="건너뛰기", command=on_no, font=("Arial", 14), fg="red")
    no_button.pack(side="left", padx=5)

def start_update(remote_commit):
    global update_notification_frame, ignore_commit
    ignore_commit = None  # '예'를 누르면 기록된 커밋을 초기화
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()
    threading.Thread(target=update_system).start()

def ignore_update(remote_commit):
    global ignore_commit, update_notification_frame
    ignore_commit = remote_commit
    with open(IGNORE_COMMIT_FILE, "w") as file:
        file.write(ignore_commit.decode())
    if update_notification_frame and update_notification_frame.winfo_exists():
        update_notification_frame.destroy()

def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)

def get_system_info():
    try:
        current_branch = subprocess.check_output(['git', 'branch', '--show-current']).strip().decode()
    except subprocess.CalledProcessError:
        current_branch = "N/A"
        
    cpu_temp = os.popen("vcgencmd measure_temp").readline().replace("temp=", "").strip()
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent
    net_io = psutil.net_io_counters()
    network_info = f"Sent: {net_io.bytes_sent / (1024 * 1024):.2f}MB, Recv: {net_io.bytes_recv / (1024 * 1024):.2f}MB"
    return f"IP: {get_ip_address()} | Branch: {current_branch} | Temp: {cpu_temp} | CPU: {cpu_usage}% | Mem: {memory_usage}% | Disk: {disk_usage}% | Net: {network_info}"

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
    # root.after(1000, update_status_label)  # 1초마다 업데이트

def change_branch():
    global branch_window
    if branch_window and branch_window.winfo_exists():
        branch_window.focus()
        return

    branch_window = Toplevel(root)
    branch_window.title("브랜치 변경")
    branch_window.attributes("-topmost", True)

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

def show_red_overlay():
    overlay = Toplevel(root)
    overlay.attributes('-fullscreen', True)
    overlay.attributes('-topmost', True)
    overlay.attributes('-alpha', 0.7)
    overlay.configure(background='red')
    overlay.bind("<Escape>", lambda e: overlay.destroy())

if __name__ == "__main__":
    root = Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    def signal_handler(sig, frame):
        print("Exiting gracefully...")
        root.destroy()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    initialize_globals(root)

    if not admin_password:
        prompt_new_password()

    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    root.bind("<Escape>", exit_fullscreen)

    modbus_boxes = settings["modbus_boxes"]
    analog_boxes = settings["analog_boxes"]

    main_frame = Frame(root)
    main_frame.grid(row=0, column=0)

    modbus_ui = ModbusUI(main_frame, modbus_boxes, settings["modbus_gas_types"])
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["analog_gas_types"])

    modbus_ui.box_frame.grid(row=0, column=0, padx=10, pady=10)
    analog_ui.box_frame.grid(row=1, column=0, padx=10, pady=10)

    settings_button = Button(root, text="⚙", command=lambda: prompt_new_password() if not admin_password else show_password_prompt(show_settings), font=("Arial", 20))
    def on_enter(event):
        event.widget.config(background="#b2b2b2", foreground="black")
    def on_leave(event):
        event.widget.config(background="#b2b2b2", foreground="black")

    settings_button.bind("<Enter>", on_enter)
    settings_button.bind("<Leave>", on_leave)

    settings_button.place(relx=1.0, rely=1.0, anchor='se')

    status_label = Label(root, text="", font=("Arial", 10))
    status_label.place(relx=0.0, rely=1.0, anchor='sw')

    def system_info_thread():
        while True:
            update_status_label()
            time.sleep(1)

    # 새로운 버튼을 추가합니다.
    overlay_button = Button(root, text="🔴", command=show_red_overlay, font=("Arial", 20))
    overlay_button.bind("<Enter>", on_enter)
    overlay_button.bind("<Leave>", on_leave)
    overlay_button.place(relx=0.95, rely=1.0, anchor='se')

    # 기록된 ignore_commit을 로드
    if os.path.exists(IGNORE_COMMIT_FILE):
        with open(IGNORE_COMMIT_FILE, "r") as file:
            ignore_commit = file.read().strip().encode()

    checking_updates = True
    threading.Thread(target=system_info_thread, daemon=True).start()
    threading.Thread(target=check_for_updates, daemon=True).start()

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
