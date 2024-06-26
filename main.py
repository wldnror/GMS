import os
import sys
import time
import threading
import socket
import psutil
import signal
import subprocess
from tkinter import Tk, Frame, Button, Label, Toplevel
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
from settings import (prompt_new_password, show_password_prompt, show_settings, load_settings, update_status_label)

settings = load_settings()
admin_password = settings.get("admin_password")

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
        prompt_new_password(root)

    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)

    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    modbus_boxes = settings["modbus_boxes"]
    analog_boxes = settings["analog_boxes"]

    main_frame = Frame(root)
    main_frame.grid(row=0, column=0)

    modbus_ui = ModbusUI(main_frame, modbus_boxes, settings["modbus_gas_types"])
    analog_ui = AnalogUI(main_frame, analog_boxes, settings["analog_gas_types"])

    modbus_ui.box_frame.grid(row=0, column=0, padx=10, pady=10)
    analog_ui.box_frame.grid(row=1, column=0, padx=10, pady=10)

    settings_button = Button(root, text="⚙", command=lambda: prompt_new_password(root) if not admin_password else show_password_prompt(root, lambda: show_settings(root)), font=("Arial", 20))

    settings_button.place(relx=1.0, rely=1.0, anchor='se')

    status_label = Label(root, text="", font=("Arial", 10))
    status_label.place(relx=0.0, rely=1.0, anchor='sw')

    def system_info_thread():
        while True:
            update_status_label(status_label)
            time.sleep(1)

    overlay_button = Button(root, text="🔴", command=show_red_overlay, font=("Arial", 20))
    overlay_button.place(relx=0.95, rely=1.0, anchor='se')

    checking_updates = True
    threading.Thread(target=system_info_thread, daemon=True).start()

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
