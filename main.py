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

# 글로벌 변수로 설정 창을 참조합니다.
settings_window = None
password_window = None
attempt_count = 0
lock_time = 0
lock_window = None
box_settings_window = None  # box_settings_window 변수를 글로벌로 선언
new_password_window = None  # 비밀번호 설정 창을 위한 글로벌 변수
update_notification_frame = None  # 업데이트 알림 프레임
ignore_commit = None  # 건너뛸 커밋
branch_window = None  # 브랜치 변경 창을 위한 글로벌 변수

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
            "modbus_gas_types": {},
            "analog_gas_types": {}
        }

def save_settings(settings):
    with open(SETTINGS_FILE, 'wb') as file:
        encrypted_data = encrypt_data(json.dumps(settings))
        file.write(encrypted_data)

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
    create_keypad(new_password_entry, new_password_window, geometry="pack")

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
    create_keypad(confirm_password_entry, new_password_window, geometry="pack")

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
    create_keypad(password_entry, password_window, geometry="pack")

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

    # "전체 화면 설정"과 "창 크기 설정" 버튼을 한 줄에 나란히 배치
    frame1 = Frame(settings_window)
    frame1.pack(pady=5)
    fullscreen_button = Button(frame1, text="전체 화면 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=enter_fullscreen)
    fullscreen_button.grid(row=0, column=0)
    windowed_button = Button(frame1, text="창 크기 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=exit_fullscreen)
    windowed_button.grid(row=0, column=1)

    # "시스템 업데이트"와 "브랜치 변경" 버튼을 한 줄에 나란히 배치
    frame2 = Frame(settings_window)
    frame2.pack(pady=5)
    update_button = Button(frame2, text="시스템 업데이트", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: threading.Thread(target=update_system).start())
    update_button.grid(row=0, column=0)
    branch_button = Button(frame2, text="브랜치 변경", font=button_font, width=12, height=2, padx=10, pady=10, command=change_branch)
    branch_button.grid(row=0, column=1)

    # "재시작" 및 "종료" 버튼을 추가하고 동일한 위치에 배치
    frame3 = Frame(settings_window)
    frame3.pack(pady=5)
    restart_button = Button(frame3, text="재시작", font=button_font, width=12, height=2, padx=10, pady=10, command=restart_application)
    restart_button.grid(row=0, column=0)
    exit_button = Button(frame3, text="종료", font=button_font, width=12, height=2, padx=10, pady=10, command=exit_application)
    exit_button.grid(row=0, column=1)

def show_box_settings():
    global box_settings_window
    if box_settings_window and box_settings_window.winfo_exists():
        box_settings_window.focus()
        return

    box_settings_window = Toplevel(root)
    box_settings_window.title("상자 설정")
    box_settings_window.attributes("-topmost", True)

    Label(box_settings_window, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
    modbus_boxes_var = StringVar(value=settings["modbus_boxes"])
    modbus_box_count = int(modbus_boxes_var.get())

    def increase_modbus_boxes():
        nonlocal modbus_box_count
        if modbus_box_count < 14:
            modbus_box_count += 1
            modbus_boxes_var.set(modbus_box_count)
            update_gas_type_options()

    def decrease_modbus_boxes():
        nonlocal modbus_box_count
        if modbus_box_count > 0:
            modbus_box_count -= 1
            modbus_boxes_var.set(modbus_box_count)
            update_gas_type_options()

    frame_modbus = Frame(box_settings_window)
    frame_modbus.grid(row=0, column=1, padx=5, pady=5)
    Button(frame_modbus, text="-", command=decrease_modbus_boxes, font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
    Label(frame_modbus, textvariable=modbus_boxes_var, font=("Arial", 12)).grid(row=0, column=1, padx=5, pady=5)
    Button(frame_modbus, text="+", command=increase_modbus_boxes, font=("Arial", 12)).grid(row=0, column=2, padx=5, pady=5)

    Label(box_settings_window, text="4~20mA 상자 수", font=("Arial", 12)).grid(row=1, column=0, padx=5, pady=5)
    analog_boxes_var = StringVar(value=settings["analog_boxes"])
    analog_box_count = int(analog_boxes_var.get())

    def increase_analog_boxes():
        nonlocal analog_box_count
        if analog_box_count < 14:
            analog_box_count += 1
            analog_boxes_var.set(analog_box_count)
            update_gas_type_options()

    def decrease_analog_boxes():
        nonlocal analog_box_count
        if analog_box_count > 0:
            analog_box_count -= 1
            analog_boxes_var.set(analog_box_count)
            update_gas_type_options()

    frame_analog = Frame(box_settings_window)
    frame_analog.grid(row=1, column=1, padx=5, pady=5)
    Button(frame_analog, text="-", command=decrease_analog_boxes, font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
    Label(frame_analog, textvariable=analog_boxes_var, font=("Arial", 12)).grid(row=0, column=1, padx=5, pady=5)
    Button(frame_analog, text="+", command=increase_analog_boxes, font=("Arial", 12)).grid(row=0, column=2, padx=5, pady=5)

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

            label.grid(row=i + 2, column=0, padx=5, pady=5)
            combo.grid(row=i + 2, column=1, padx=5, pady=5)

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

            label.grid(row=i + 2, column=2, padx=5, pady=5)
            combo.grid(row=i + 2, column=3, padx=5, pady=5)

    update_gas_type_options()

    def save_and_close():
        try:
            modbus_boxes = int(modbus_boxes_var.get())
            analog_boxes = int(analog_boxes_var.get())
            if modbus_boxes + analog_boxes > 14:
                messagebox.showerror("입력 오류", "상자의 총합이 14개를 초과할 수 없습니다.")
                return
            settings["modbus_boxes"] = modbus_boxes
            settings["analog_boxes"] = analog_boxes
            for i, var in enumerate(modbus_gas_type_vars):
                settings["modbus_gas_types"][f"modbus_box_{i}"] = var.get()
            for i, var in enumerate(analog_gas_type_vars):
                settings["analog_gas_types"][f"analog_box_{i}"] = var.get()
            save_settings(settings)
            messagebox.showinfo("설정 저장", "설정이 저장되었습니다.")
            box_settings_window.destroy()
            restart_application()  # 설정이 변경되면 애플리케이션을 재시작
        except ValueError:
            messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=16, columnspan=4, pady=10)

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
    root.after(1000, update_status_label)  # 1초마다 업데이트

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
