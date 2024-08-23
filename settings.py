from tkinter import Toplevel, Label, Entry, Button, Frame, messagebox, StringVar
from tkinter import ttk
import json
import os
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
audio_selection_window = None  # 오디오 선택 창 전역 변수
root = None
selected_audio_file = None

def initialize_globals(main_root, change_branch_func):
    global root, change_branch
    root = main_root
    change_branch = change_branch_func

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'rb') as file:
            decrypted_data = utils.decrypt_data(file.read())
        return json.loads(decrypted_data)
    else:
        return {
            "modbus_boxes": 0,
            "analog_boxes": 0,
            "admin_password": None,
            "modbus_gas_types": {},
            "analog_gas_types": {},
            "audio_file": None
        }

def save_settings(settings):
    with open(SETTINGS_FILE, 'wb') as file:
        encrypted_data = utils.encrypt_data(json.dumps(settings))
        file.write(encrypted_data)

settings = load_settings()
admin_password = settings.get("admin_password")
selected_audio_file = settings.get("audio_file")

def show_box_settings():
    global box_settings_window
    if box_settings_window and box_settings_window.winfo_exists():
        box_settings_window.focus()
        return

    box_settings_window = Toplevel(root)
    box_settings_window.title("상자 설정")
    box_settings_window.attributes("-topmost", True)

    Label(box_settings_window, text="Modbus TCP 상자 수", font=("Arial", 12)).grid(row=0, column=0)
    modbus_boxes_var = StringVar(value=str(settings.get("modbus_boxes", 0)))
    analog_boxes_var = StringVar(value=str(settings.get("analog_boxes", 0)))

    def modify_box_count(var, delta):
        current_value = int(var.get())
        new_value = current_value + delta
        if 0 <= new_value <= 14:  # 상자 수는 0에서 14 사이로 제한
            var.set(str(new_value))

    frame_modbus = Frame(box_settings_window)
    frame_modbus.grid(row=0, column=1)
    Button(frame_modbus, text="-", command=lambda: modify_box_count(modbus_boxes_var, -1), font=("Arial", 12)).grid(row=0, column=0)
    Label(frame_modbus, textvariable=modbus_boxes_var, font=("Arial", 12)).grid(row=0, column=1)
    Button(frame_modbus, text="+", command=lambda: modify_box_count(modbus_boxes_var, 1), font=("Arial", 12)).grid(row=0, column=2)

    Label(box_settings_window, text="4~20mA 상자 수", font=("Arial", 12)).grid(row=1, column=0)
    frame_analog = Frame(box_settings_window)
    frame_analog.grid(row=1, column=1)
    Button(frame_analog, text="-", command=lambda: modify_box_count(analog_boxes_var, -1), font=("Arial", 12)).grid(row=0, column=0)
    Label(frame_analog, textvariable=analog_boxes_var, font=("Arial", 12)).grid(row=0, column=1)
    Button(frame_analog, text="+", command=lambda: modify_box_count(analog_boxes_var, 1), font=("Arial", 12)).grid(row=0, column=2)

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

            label.grid(row=i + 2, column=0)
            combo.grid(row=i + 2, column=1)

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

            label.grid(row=i + 2, column=2)
            combo.grid(row=i + 2, column=3)

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
            utils.restart_application()
        except ValueError:
            messagebox.showerror("입력 오류", "올바른 숫자를 입력하세요.")

    Button(box_settings_window, text="저장", command=save_and_close, font=("Arial", 12), width=15, height=2).grid(row=16, columnspan=4, pady=10)
