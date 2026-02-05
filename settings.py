from tkinter import Toplevel, Label, Entry, Button, Frame, StringVar, Checkbutton, IntVar
from tkinter import ttk
import json
import os
import threading
import subprocess
import time
import utils
import tkinter as tk

SETTINGS_FILE = "settings.json"

settings_window = None
password_window = None
attempt_count = 0
lock_time = 0
lock_window = None
box_settings_window = None
new_password_window = None
branch_window = None
audio_selection_window = None
root = None
selected_audio_file = None

on_fw_file_all = None
on_fw_upgrade_all = None

def initialize_globals(main_root, change_branch_func):
    global root, change_branch
    root = main_root
    change_branch = change_branch_func

key = utils.load_key()
cipher_suite = utils.cipher_suite

def encrypt_data(data):
    return utils.encrypt_data(data)

def decrypt_data(data):
    return utils.decrypt_data(data)

def toast(msg, duration=1800, bg="#222222", fg="white"):
    if root is None or not root.winfo_exists():
        return

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)

    frm = tk.Frame(win, bg=bg, bd=1, relief="solid")
    frm.pack(fill="both", expand=True)

    lbl = tk.Label(frm, text=msg, bg=bg, fg=fg, font=("Arial", 12))
    lbl.pack(padx=14, pady=10)

    root.update_idletasks()

    w = win.winfo_reqwidth()
    h = win.winfo_reqheight()

    rx = root.winfo_rootx()
    ry = root.winfo_rooty()
    rw = root.winfo_width()
    rh = root.winfo_height()

    x = rx + rw - w - 20
    y = ry + rh - h - 20
    win.geometry(f"{w}x{h}+{x}+{y}")

    def _close():
        if win.winfo_exists():
            win.destroy()

    win.after(duration, _close)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "rb") as file:
            encrypted_data = file.read()
        decrypted_data = decrypt_data(encrypted_data)
        return json.loads(decrypted_data)
    return {
        "modbus_boxes": 0,
        "analog_boxes": 0,
        "admin_password": None,
        "modbus_gas_types": {},
        "analog_gas_types": {},
        "audio_file": None,
        "battery_box_enabled": 0,
    }

def save_settings(settings):
    with open(SETTINGS_FILE, "wb") as file:
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
    global new_password_window, settings, admin_password
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
        nonlocal new_password
        confirm_password = confirm_password_entry.get()
        if new_password == confirm_password and new_password:
            settings["admin_password"] = new_password
            save_settings(settings)
            admin_password = new_password
            toast("새로운 비밀번호가 설정되었습니다.", bg="#1f4f1f")
            new_password_window.destroy()
            utils.restart_application()
        else:
            toast("비밀번호가 일치하지 않습니다.", bg="#7a1f1f")
            new_password_window.destroy()
            prompt_new_password()

    Button(new_password_window, text="저장", command=save_new_password).pack(pady=5)

def show_password_prompt(callback):
    global attempt_count, lock_time, password_window, settings_window, lock_window, admin_password

    settings = load_settings()
    admin_password = settings.get("admin_password")

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

    msg_label = Label(password_window, text="", font=("Arial", 12), fg="red")
    msg_label.pack(pady=5)

    def check_password():
        global attempt_count, lock_time
        if password_entry.get() == admin_password:
            password_window.destroy()
            callback()
        else:
            attempt_count += 1
            if attempt_count >= 5:
                lock_time = time.time() + 60
                attempt_count = 0
                password_window.destroy()
                show_password_prompt(callback)
            else:
                msg_label.config(text="비밀번호가 틀렸습니다.")

    Button(password_window, text="확인", command=check_password).pack(pady=5)

def _call(cb):
    if cb:
        cb()
    else:
        toast("기능이 연결되지 않았습니다.", bg="#7a1f1f")

def show_settings():
    global settings_window, selected_audio_file, settings, admin_password

    settings = load_settings()
    admin_password = settings.get("admin_password")
    selected_audio_file = settings.get("audio_file")

    if settings_window and settings_window.winfo_exists():
        settings_window.focus()
        return

    settings_window = Toplevel(root)
    settings_window.title("설정 메뉴")
    settings_window.attributes("-topmost", True)

    Label(settings_window, text="GMS-1000 설정", font=("Arial", 16)).pack(pady=10)

    button_font = ("Arial", 14)
    button_style = {"font": button_font, "width": 25, "height": 2, "padx": 10, "pady": 10}

    Button(settings_window, text="상자 설정", command=show_box_settings, **button_style).pack(pady=5)
    Button(settings_window, text="비밀번호 변경", command=prompt_new_password, **button_style).pack(pady=5)

    Button(settings_window, text="FW 파일 전체 적용", command=lambda: _call(on_fw_file_all), **button_style).pack(pady=5)
    Button(settings_window, text="전체 FW 업데이트", command=lambda: _call(on_fw_upgrade_all), **button_style).pack(pady=5)

    frame1 = Frame(settings_window)
    frame1.pack(pady=5)
    Button(frame1, text="전체 화면 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: utils.enter_fullscreen(root)).grid(row=0, column=0)
    Button(frame1, text="창 크기 설정", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: utils.exit_fullscreen(root)).grid(row=0, column=1)

    frame2 = Frame(settings_window)
    frame2.pack(pady=5)

    def _update_thread():
        check_and_update_system()

    Button(frame2, text="시스템 업데이트", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: threading.Thread(target=_update_thread, daemon=True).start()).grid(row=0, column=0)
    Button(frame2, text="브랜치 변경", font=button_font, width=12, height=2, padx=10, pady=10, command=change_branch).grid(row=0, column=1)

    frame3 = Frame(settings_window)
    frame3.pack(pady=5)
    Button(frame3, text="재시작", font=button_font, width=12, height=2, padx=10, pady=10, command=utils.restart_application).grid(row=0, column=0)
    Button(frame3, text="종료", font=button_font, width=12, height=2, padx=10, pady=10, command=lambda: utils.exit_application(root)).grid(row=0, column=1)

    selected_audio_label = Label(settings_window, text=f"선택된 오디오 파일: {os.path.basename(selected_audio_file) if selected_audio_file else 'None'}", font=("Arial", 12))
    selected_audio_label.pack(pady=10)

    def select_audio_file():
        global selected_audio_file, audio_selection_window, settings

        if audio_selection_window and audio_selection_window.winfo_exists():
            audio_selection_window.focus()
            return

        audio_folder = "audio"
        if not os.path.isdir(audio_folder):
            toast("audio 폴더가 없습니다.", bg="#7a1f1f")
            return

        audio_files = [f for f in os.listdir(audio_folder) if f.endswith((".mp3", ".wav"))]
        if not audio_files:
            toast("audio 폴더에 mp3/wav 파일이 없습니다.", bg="#7a1f1f")
            return

        def on_audio_select(event):
            global selected_audio_file
            selected_audio_file = os.path.join(audio_folder, audio_combo.get())
            settings["audio_file"] = selected_audio_file
            save_settings(settings)
            selected_audio_label.config(text=f"선택된 오디오 파일: {os.path.basename(selected_audio_file)}")
            toast(f"오디오 선택: {os.path.basename(selected_audio_file)}", bg="#1f4f1f")
            audio_selection_window.destroy()

        audio_selection_window = Toplevel(settings_window)
        audio_selection_window.title("오디오 파일 선택")
        audio_selection_window.attributes("-topmost", True)
        Label(audio_selection_window, text="오디오 파일을 선택하세요", font=("Arial", 12)).pack(pady=10)
        audio_combo = ttk.Combobox(audio_selection_window, values=audio_files, font=("Arial", 12))
        audio_combo.pack(pady=5)
        audio_combo.bind("<<ComboboxSelected>>", on_audio_select)

    Button(settings_window, text="경고 오디오 선택", command=select_audio_file, **button_style).pack(pady=5)

def check_and_update_system():
    try:
        current_branch = subprocess.check_output(["git", "branch", "--show-current"]).strip().decode()
        local_commit = subprocess.check_output(["git", "rev-parse", "HEAD"]).strip()
        remote_commit = subprocess.check_output(["git", "ls-remote", "origin", current_branch]).split()[0]

        if local_commit != remote_commit:
            toast("새로운 업데이트가 있습니다. 업데이트를 시작합니다.", bg="#1f4f7a")
            utils.update_system(root)
        else:
            toast("현재 최신 버전입니다.", bg="#1f4f1f")
    except Exception as e:
        toast(f"업데이트 확인 오류: {e}", bg="#7a1f1f")

def show_box_settings():
    global box_settings_window, settings

    settings = load_settings()

    if box_settings_window and box_settings_window.winfo_exists():
        box_settings_window.attributes("-topmost", True)
        box_settings_window.focus()
        return

    box_settings_window = Toplevel(root)
    box_settings_window.title("상자 설정")
    box_settings_window.attributes("-topmost", True)

    Label(box_settings_window, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(row=0, column=0, padx=2, pady=2, sticky="w")
    modbus_boxes_var = StringVar(value=str(settings.get("modbus_boxes", 0)))
    analog_boxes_var = StringVar(value=str(settings.get("analog_boxes", 0)))

    battery_box_var = IntVar(value=settings.get("battery_box_enabled", 0))
    battery_box_check = Checkbutton(box_settings_window, text="배터리 박스 활성화", variable=battery_box_var, font=("Arial", 12))
    battery_box_check.grid(row=0, column=2, padx=2, pady=2, sticky="e")

    def check_total_boxes():
        try:
            modbus_boxes = int(modbus_boxes_var.get())
            analog_boxes = int(analog_boxes_var.get())
            battery_box = int(battery_box_var.get())
        except ValueError:
            return

        total_boxes = modbus_boxes + analog_boxes + battery_box
        if total_boxes > 12:
            if battery_box_var.get() == 1:
                battery_box_var.set(0)
            toast("상자의 총합이 12개를 초과할 수 없습니다.", bg="#7a1f1f")

    battery_box_var.trace_add("write", lambda *args: check_total_boxes())

    def modify_box_count(var, delta):
        try:
            current_value = int(var.get())
            other_value = int(analog_boxes_var.get() if var == modbus_boxes_var else modbus_boxes_var.get())
            battery_box = int(battery_box_var.get())
        except ValueError:
            toast("올바른 숫자를 입력하세요.", bg="#7a1f1f")
            return

        new_value = current_value + delta
        total_boxes = new_value + other_value + battery_box
        if 0 <= new_value <= 12 and total_boxes <= 12:
            var.set(str(new_value))
        else:
            toast("상자의 총합이 12개를 초과할 수 없습니다.", bg="#7a1f1f")

    frame_modbus = Frame(box_settings_window)
    frame_modbus.grid(row=0, column=1, padx=2, pady=2)
    Button(frame_modbus, text="-", command=lambda: modify_box_count(modbus_boxes_var, -1), font=("Arial", 12)).grid(row=0, column=0, padx=2, pady=2)
    Label(frame_modbus, textvariable=modbus_boxes_var, font=("Arial", 12)).grid(row=0, column=1, padx=2, pady=2)
    Button(frame_modbus, text="+", command=lambda: modify_box_count(modbus_boxes_var, 1), font=("Arial", 12)).grid(row=0, column=2, padx=2, pady=2)

    Label(box_settings_window, text="4~20mA 상자 수", font=("Arial", 12)).grid(row=1, column=0, padx=2, pady=2, sticky="w")
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

        try:
            modbus_boxes = int(modbus_boxes_var.get())
            analog_boxes = int(analog_boxes_var.get())
        except ValueError:
            return

        for i in range(modbus_boxes):
            if len(modbus_gas_type_combos) <= i:
                modbus_gas_type_var = StringVar(value=settings.get("modbus_gas_types", {}).get(f"modbus_box_{i}", "ORG"))
                modbus_gas_type_vars.append(modbus_gas_type_var)
                combo = ttk.Combobox(box_settings_window, textvariable=modbus_gas_type_var, values=gas_type_labels, font=("Arial", 12))
                modbus_gas_type_combos.append(combo)
                label = Label(box_settings_window, text=f"Modbus 상자 {i + 1} 유형", font=("Arial", 12))
                modbus_labels.append(label)
            else:
                combo = modbus_gas_type_combos[i]
                label = modbus_labels[i]

            label.grid(row=i + 2, column=0, padx=2, pady=2, sticky="w")
            combo.grid(row=i + 2, column=1, padx=2, pady=2)

        for i in range(analog_boxes):
            if len(analog_gas_type_combos) <= i:
                analog_gas_type_var = StringVar(value=settings.get("analog_gas_types", {}).get(f"analog_box_{i}", "ORG"))
                analog_gas_type_vars.append(analog_gas_type_var)
                combo = ttk.Combobox(box_settings_window, textvariable=analog_gas_type_var, values=gas_type_labels, font=("Arial", 12))
                analog_gas_type_combos.append(combo)
                label = Label(box_settings_window, text=f"4~20mA 상자 {i + 1} 유형", font=("Arial", 12))
                analog_labels.append(label)
            else:
                combo = analog_gas_type_combos[i]
                label = analog_labels[i]

            label.grid(row=i + 2, column=2, padx=2, pady=2, sticky="w")
            combo.grid(row=i + 2, column=3, padx=2, pady=2)

        check_total_boxes()

    modbus_boxes_var.trace_add("write", lambda *args: update_gas_type_options())
    analog_boxes_var.trace_add("write", lambda *args: update_gas_type_options())

    update_gas_type_options()

    def save_and_close():
        global settings
        try:
            modbus_boxes = int(modbus_boxes_var.get())
            analog_boxes = int(analog_boxes_var.get())
            battery_box = int(battery_box_var.get())
        except ValueError:
            toast("올바른 숫자를 입력하세요.", bg="#7a1f1f")
            return

        if modbus_boxes + analog_boxes + battery_box > 12:
            toast("상자의 총합이 12개를 초과할 수 없습니다.", bg="#7a1f1f")
            return

        settings["modbus_boxes"] = modbus_boxes
        settings["analog_boxes"] = analog_boxes
        settings["battery_box_enabled"] = battery_box

        settings.setdefault("modbus_gas_types", {})
        settings.setdefault("analog_gas_types", {})

        for i, var in enumerate(modbus_gas_type_vars):
            settings["modbus_gas_types"][f"modbus_box_{i}"] = var.get()
        for i, var in enumerate(analog_gas_type_vars):
            settings["analog_gas_types"][f"analog_box_{i}"] = var.get()

        save_settings(settings)
        toast("설정이 저장되었습니다. 재시작합니다.", bg="#1f4f1f")
        box_settings_window.destroy()
        utils.restart_application()

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=16, columnspan=4, pady=10)
