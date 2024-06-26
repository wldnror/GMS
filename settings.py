import os
import json
import threading
import subprocess
import time
from tkinter import Toplevel, Label, Entry, Button, messagebox, StringVar, Frame
from tkinter import ttk
import psutil

# 설정 값을 저장할 파일 경로
SETTINGS_FILE = "settings.json"
KEY_FILE = "secret.key"
IGNORE_COMMIT_FILE = "ignore_commit.txt"

# 글로벌 변수들
settings_window = None
password_window = None
new_password_window = None
update_notification_frame = None
branch_window = None
lock_time = 0
attempt_count = 0

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as file:
                return json.load(file)
    except (json.JSONDecodeError, IOError):
        pass
    return {
        "modbus_boxes": 14,
        "analog_boxes": 0,
        "admin_password": None,
        "modbus_gas_types": {},
        "analog_gas_types": {}
    }


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as file:
            json.dump(settings, file, indent=4)
    except IOError as e:
        messagebox.showerror("저장 오류", f"설정을 저장하는 중 오류가 발생했습니다: {e}")

settings = load_settings()
admin_password = settings.get("admin_password")

def prompt_new_password(root):
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

    def confirm_password():
        new_password = new_password_entry.get()
        new_password_window.destroy()
        prompt_confirm_password(root, new_password)

    Button(new_password_window, text="다음", command=confirm_password).pack(pady=5)

def prompt_confirm_password(root, new_password):
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
            prompt_new_password(root)

    Button(new_password_window, text="저장", command=save_new_password).pack(pady=5)

def show_password_prompt(root, callback):
    global attempt_count, lock_time, password_window, settings_window

    if time.time() < lock_time:
        messagebox.showerror("잠금", f"비밀번호 입력 시도가 5회 초과되었습니다. {int(lock_time - time.time())}초 후에 다시 시도하십시오.")
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
                show_password_prompt(root, callback)
            else:
                Label(password_window, text="비밀번호가 틀렸습니다.", font=("Arial", 12), fg="red").pack(pady=5)

    Button(password_window, text="확인", command=check_password).pack(pady=5)

def show_settings(root):
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

    Button(settings_window, text="상자 설정", command=lambda: show_box_settings(root), **button_style).pack(pady=5)
    Button(settings_window, text="비밀번호 변경", command=lambda: prompt_new_password(root), **button_style).pack(pady=5)

    frame1 = Frame(settings_window)
    frame1.pack(pady=5)
    fullscreen_button = Button(frame1, text="전체 화면 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=enter_fullscreen)
    fullscreen_button.grid(row=0, column=0)
    windowed_button = Button(frame1, text="창 크기 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=exit_fullscreen)
    windowed_button.grid(row=0, column=1)

    frame2 = Frame(settings_window)
    frame2.pack(pady=5)
    update_button = Button(frame2, text="시스템 업데이트", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: threading.Thread(target=update_system).start())
    update_button.grid(row=0, column=0)
    branch_button = Button(frame2, text="브랜치 변경", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: change_branch(root))
    branch_button.grid(row=0, column=1)

    frame3 = Frame(settings_window)
    frame3.pack(pady=5)
    restart_button = Button(frame3, text="재시작", font=button_font, width=12, height=2, padx=10, pady=10, command=restart_application)
    restart_button.grid(row=0, column=0)
    exit_button = Button(frame3, text="종료", font=button_font, width=12, height=2, padx=10, pady=10, command=exit_application)
    exit_button.grid(row=0, column=1)

def show_box_settings(root):
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

def update_status_label(status_label):
    status_label.config(text=get_system_info())
    status_label.after(1000, lambda: update_status_label(status_label))

def change_branch(root):
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
