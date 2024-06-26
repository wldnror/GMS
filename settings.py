# settings.py
from tkinter import Toplevel, Label, Entry, Button, Frame, messagebox, StringVar
from tkinter import ttk
import json
import os
import sys
import threading
import subprocess
import time
import utils

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
root = None

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
            utils.restart_application()  # 설정이 변경되면 애플리케이션을 재시작
        except ValueError:
            messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=16, columnspan=4, pady=10)
