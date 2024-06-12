from tkinter import Tk, Frame, Button, Toplevel, Label, Entry, messagebox
import random
import time
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
import signal
import sys
import subprocess
import os

# 글로벌 변수로 설정 창을 참조합니다.
settings_window = None
attempt_count = 0
lock_time = 0

# 설정 페이지를 여는 함수
def show_settings():
    global settings_window
    # 이미 설정 창이 열려 있는 경우, 창을 포커스로 가져옵니다.
    if settings_window and settings_window.winfo_exists():
        settings_window.focus()
        return

    settings_window = Toplevel(root)
    settings_window.title("Settings")
    settings_window.attributes("-topmost", True)  # 창이 항상 최상위에 위치하도록 설정합니다.

    Label(settings_window, text="GMS-1000 설정", font=("Arial", 16)).pack(pady=10)

    Button(settings_window, text="창 크기", command=exit_fullscreen).pack(pady=5)
    Button(settings_window, text="완전 전체화면", command=enter_fullscreen).pack(pady=5)
    Button(settings_window, text="시스템 업데이트", command=update_system).pack(pady=5)
    Button(settings_window, text="애플리케이션 종료", command=exit_application).pack(pady=5)

# 전체 화면 해제
def exit_fullscreen(event=None):
    root.attributes("-fullscreen", False)
    root.attributes("-topmost", False)  # 전체 화면 해제 시 최상위 속성도 해제

# 전체 화면 설정
def enter_fullscreen(event=None):
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)  # 전체 화면 모드에서는 최상위 속성 설정

# 애플리케이션 종료
def exit_application():
    root.destroy()
    sys.exit(0)

# 시스템 업데이트
def update_system():
    try:
        # 로컬 리포지토리의 최신 커밋 해시를 가져옵니다.
        local_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
        # 원격 리포지토리의 최신 커밋 해시를 가져옵니다.
        remote_commit = subprocess.check_output(['git', 'ls-remote', 'origin', 'HEAD']).split()[0]

        if local_commit == remote_commit:
            # 최신 버전일 경우
            Label(settings_window, text="이미 최신 버전입니다.", font=("Arial", 12)).pack(pady=5)
        else:
            # 업데이트가 있는 경우
            result = subprocess.run(['git', 'pull'], capture_output=True, text=True)
            Label(settings_window, text="업데이트 완료. 애플리케이션을 재시작합니다.", font=("Arial", 12)).pack(pady=5)
            root.after(2000, restart_application)  # 2초 후에 애플리케이션 재시작
    except Exception as e:
        Label(settings_window, text=f"업데이트 중 오류 발생: {e}", font=("Arial", 12)).pack(pady=5)

# 애플리케이션 재시작
def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)

# 비밀번호 입력 창을 표시하는 함수
def show_password_prompt():
    global attempt_count, lock_time

    if time.time() < lock_time:
        messagebox.showerror("잠금", "비밀번호 입력 시도가 5회 초과되었습니다. 30초 후에 다시 시도하십시오.")
        return

    password_window = Toplevel(root)
    password_window.title("비밀번호 입력")
    password_window.attributes("-topmost", True)

    Label(password_window, text="비밀번호를 입력하세요", font=("Arial", 12)).pack(pady=10)
    password_entry = Entry(password_window, show="*", font=("Arial", 12))
    password_entry.pack(pady=5)

    def create_keypad():
        frame = Frame(password_window)
        frame.pack()

        buttons = [
            '1', '2', '3',
            '4', '5', '6',
            '7', '8', '9',
            '0', 'CLR', 'DEL'
        ]

        rows = 4
        cols = 3
        for i, button in enumerate(buttons):
            b = Button(frame, text=button, width=5, height=2,
                       command=lambda b=button: on_button_click(b, password_entry))
            b.grid(row=i // cols, column=i % cols, padx=5, pady=5)

    def on_button_click(char, entry):
        if char == 'DEL':
            current_text = entry.get()
            entry.delete(0, tk.END)
            entry.insert(0, current_text[:-1])
        elif char == 'CLR':
            entry.delete(0, tk.END)
        else:
            entry.insert(tk.END, char)

    def check_password():
        nonlocal attempt_count, lock_time
        if password_entry.get() == "00700":  # 비밀번호 설정
            password_window.destroy()
            show_settings()
        else:
            attempt_count += 1
            if attempt_count >= 5:
                lock_time = time.time() + 30  # 30초 잠금
                attempt_count = 0
                password_window.destroy()
                messagebox.showerror("잠금", "비밀번호 입력 시도가 5회 초과되었습니다. 30초 후에 다시 시도하십시오.")
            else:
                Label(password_window, text="비밀번호가 틀렸습니다.", font=("Arial", 12), fg="red").pack(pady=5)

    create_keypad()
    Button(password_window, text="확인", command=check_password).pack(pady=5)

if __name__ == "__main__":
    root = Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    # 전체 화면 설정
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)  # 전체 화면 모드에서는 최상위 속성 설정

    # 중앙 정렬 설정
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # ESC 키를 눌러 전체 화면 모드를 종료할 수 있도록 이벤트를 설정합니다.
    root.bind("<Escape>", exit_fullscreen)

    # 정상 종료를 위한 핸들러 추가
    def signal_handler(sig, frame):
        print("Exiting gracefully...")
        root.destroy()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    modbus_boxes = 14  # 원하는 Modbus TCP 상자 수를 설정하세요.
    analog_boxes = 0  # 원하는 4~20mA 상자 수를 설정하세요.

    main_frame = Frame(root)
    main_frame.grid(row=0, column=0)

    # 각 UI의 부모를 main_frame으로 설정
    modbus_ui = ModbusUI(main_frame, modbus_boxes)
    analog_ui = AnalogUI(main_frame, analog_boxes)

    modbus_ui.box_frame.grid(row=0, column=0, padx=10, pady=10)  # ModbusUI 배치
    analog_ui.box_frame.grid(row=1, column=0, padx=10, pady=10)  # AnalogUI 배치

    # 톱니바퀴 버튼 추가
    settings_button = Button(root, text="⚙", command=show_password_prompt, font=("Arial", 20))
    # 마우스 오버 이벤트 핸들러
    def on_enter(event):
        event.widget.config(background="#b2b2b2", foreground="black")
    def on_leave(event):
        event.widget.config(background="#b2b2b2", foreground="black")
    # 이벤트 바인딩
    settings_button.bind("<Enter>", on_enter)
    settings_button.bind("<Leave>", on_leave)

    settings_button.place(relx=1.0, rely=1.0, anchor='se')

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
