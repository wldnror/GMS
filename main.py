main.py


import json
import os
import time
from tkinter import Tk, Frame, Button, Toplevel, Label, Entry, messagebox, StringVar, OptionMenu
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

# 글로벌 변수로 설정 창을 참조합니다.
settings_window = None
password_window = None
attempt_count = 0
lock_time = 0
lock_window = None
box_settings_window = None  # box_settings_window 변수를 글로벌로 선언
new_password_window = None  # 비밀번호 설정 창을 위한 글로벌 변수

# 설정 값을 저장할 파일 경로
SETTINGS_FILE = "settings.json"
KEY_FILE = "secret.key"

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

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'rb') as file:
            encrypted_data = file.read()
        decrypted_data = decrypt_data(encrypted_data)
        return json.loads(decrypted_data)
    else:
        return {
            "modbus_boxes": 14,
            "analog_boxes": 0,
            "admin_password": None,
            "gas_types": {}
        }

def save_settings(settings):
    with open(SETTINGS_FILE, 'wb') as file:
        encrypted_data = encrypt_data(json.dumps(settings))
        file.write(encrypted_data)

settings = load_settings()
admin_password = settings.get("admin_password")

def create_keypad(entry):
    keypad_frame = Frame(entry.master)
    keypad_frame.grid(row=1, column=0, columnspan=2)

    def on_button_click(char):
        if char == 'DEL':
            current_text = entry.get()
            entry.delete(0, tk.END)
            entry.insert(0, current_text[:-1])
        elif char == 'CLR':
            entry.delete(0, tk.END)
        else:
            entry.insert(tk.END, char)

    buttons = [str(i) for i in range(10)]
    random.shuffle(buttons)
    buttons.append('CLR')
    buttons.append('DEL')

    rows = 4
    cols = 3
    for i, button in enumerate(buttons):
        b = Button(keypad_frame, text=button, width=5, height=2,
                   command=lambda b=button: on_button_click(b))
        b.grid(row=i // cols, column=i % cols, padx=5, pady=5)
    
    return keypad_frame

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
    create_keypad(new_password_entry)

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
    create_keypad(confirm_password_entry)

    def save_new_password():
        confirm_password = confirm_password_entry.get()
        if new_password == confirm_password and new_password:
            settings["admin_password"] = new_password
            save_settings(settings)
            messagebox.showinfo("비밀번호 설정", "새로운 비밀번호가 설정되었습니다.")
            new_password_window.destroy()
            restart_application()
        else:
            messagebox.showerror("비밀번호 오류", "비밀번호가 일치하지 않거나 유효하지 않습니다.")
            new_password_window.destroy()
            prompt_new_password()

    Button(new_password_window, text="저장", command=save_new_password).pack(pady=5)

def show_password_prompt():
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
    create_keypad(password_entry)

    def check_password():
        global attempt_count, lock_time
        if password_entry.get() == admin_password:
            password_window.destroy()
            show_settings()
        else:
            attempt_count += 1
            if attempt_count >= 5:
                lock_time = time.time() + 60  # 60초 잠금
                attempt_count = 0
                password_window.destroy()
                show_password_prompt()
            else:
                Label(password_window, text="비밀번호가 틀렸습니다.", font=("Arial", 12), fg="red").pack(pady=5)

    Button(password_window, text="확인", command=check_password).pack(pady=5)

def show_settings():
    global settings_window
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
    Button(settings_window, text="창 크기 설정", command=exit_fullscreen, **button_style).pack(pady=5)
    Button(settings_window, text="전체 화면 설정", command=enter_fullscreen, **button_style).pack(pady=5)
    Button(settings_window, text="시스템 업데이트", command=lambda: threading.Thread(target=update_system).start(), **button_style).pack(pady=5)
    Button(settings_window, text="애플리케이션 종료", command=exit_application, **button_style).pack(pady=5)

def show_box_settings():
    global box_settings_window
    if box_settings_window and box_settings_window.winfo_exists():
        box_settings_window.focus()
        return

    box_settings_window = Toplevel(root)
    box_settings_window.title("상자 설정")
    box_settings_window.attributes("-topmost", True)

    Label(box_settings_window, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
    modbus_entry = Entry(box_settings_window, font=("Arial", 12))
    modbus_entry.insert(0, settings["modbus_boxes"])
    modbus_entry.grid(row=0, column=1, padx=5, pady=5)

    Label(box_settings_window, text="4~20mA 상자 수", font=("Arial", 12)).grid(row=1, column=0, padx=5, pady=5)
    analog_entry = Entry(box_settings_window, font=("Arial", 12))
    analog_entry.insert(0, settings["analog_boxes"])
    analog_entry.grid(row=1, column=1, padx=5, pady=5)

    create_keypad(modbus_entry)
    create_keypad(analog_entry)

    gas_type_labels = ["ORG", "ARF-T", "HMDS", "HC-100"]
    gas_type_vars = []

    for i in range(max(settings["modbus_boxes"], settings["analog_boxes"])):
        gas_type_var = StringVar(value=settings["gas_types"].get(f"box_{i}", "ORG"))
        gas_type_vars.append(gas_type_var)
        Label(box_settings_window, text=f"상자 {i + 1} 유형", font=("Arial", 12)).grid(row=i + 2, column=0, padx=5, pady=5)
        OptionMenu(box_settings_window, gas_type_var, *gas_type_labels).grid(row=i + 2, column=1, padx=5, pady=5)

    def save_and_close():
        try:
            settings["modbus_boxes"] = int(modbus_entry.get())
            settings["analog_boxes"] = int(analog_entry.get())
            for i, var in enumerate(gas_type_vars):
                settings["gas_types"][f"box_{i}"] = var.get()
            save_settings(settings)
            messagebox.showinfo("설정 저장", "설정이 저장되었습니다.")
            box_settings_window.destroy()
            restart_application()  # 설정이 변경되면 애플리케이션을 재시작
        except ValueError:
            messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=max(settings["modbus_boxes"], settings["analog_boxes"]) + 2, columnspan=2, pady=10)

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
    try:
        local_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
        remote_commit = subprocess.check_output(['git', 'ls-remote', 'origin', 'HEAD']).split()[0]

        if local_commit == remote_commit:
            message = "이미 최신 버전입니다."
        else:
            result = subprocess.run(['git', 'pull'], capture_output=True, text=True)
            message = "업데이트 완료. 애플리케이션을 재시작합니다."
            root.after(2000, restart_application)
    except Exception as e:
        message = f"업데이트 중 오류 발생: {e}"
    
    messagebox.showinfo("시스템 업데이트", message)

def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)

def get_system_info():
    cpu_temp = os.popen("vcgencmd measure_temp").readline().replace("temp=", "").strip()
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent
    net_io = psutil.net_io_counters()
    network_info = f"Sent: {net_io.bytes_sent / (1024 * 1024):.2f}MB, Recv: {net_io.bytes_recv / (1024 * 1024):.2f}MB"
    return f"IP: {get_ip_address()} | Temp: {cpu_temp} | CPU: {cpu_usage}% | Mem: {memory_usage}% | Disk: {disk_usage}% | Net: {network_info}"

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

if __name__ == "__main__":
    root = Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    root.bind("<Escape>", exit_fullscreen)

    def signal_handler(sig, frame):
        print("Exiting gracefully...")
        root.destroy()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    if not admin_password:
        prompt_new_password()

    modbus_boxes = settings["modbus_boxes"]
    analog_boxes = settings["analog_boxes"]

    main_frame = Frame(root)
    main_frame.grid(row=0, column=0)

    modbus_ui = ModbusUI(main_frame, modbus_boxes, settings["gas_types"])
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["gas_types"])

    modbus_ui.box_frame.grid(row=0, column=0, padx=10, pady=10)
    analog_ui.box_frame.grid(row=1, column=0, padx=10, pady=10)

    settings_button = Button(root, text="⚙", command=show_password_prompt, font=("Arial", 20))
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

    threading.Thread(target=system_info_thread, daemon=True).start()

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
