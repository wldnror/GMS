from tkinter import Tk, Frame, Button, Menu, Toplevel, Label
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
import signal
import sys
import subprocess
import os  # os 모듈을 추가로 가져옵니다.

# 글로벌 변수로 설정 창을 참조합니다.
settings_window = None

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

def exit_fullscreen(event=None):
    root.attributes("-fullscreen", False)

def enter_fullscreen(event=None):
    root.attributes("-fullscreen", True)

def exit_application():
    root.destroy()
    sys.exit(0)

def update_system():
    try:
        # git pull 명령 실행
        result = subprocess.run(['git', 'pull'], capture_output=True, text=True)
        output = result.stdout

        if result.returncode == 0 and "Already up to date." in output:
            # 최신 버전일 경우
            Label(settings_window, text="이미 최신 버전입니다.", font=("Arial", 12)).pack(pady=5)
        else:
            # 업데이트가 있는 경우
            Label(settings_window, text="업데이트 완료. 애플리케이션을 재시작합니다.", font=("Arial", 12)).pack(pady=5)
            root.after(2000, restart_application)  # 2초 후에 애플리케이션 재시작
    except Exception as e:
        Label(settings_window, text=f"업데이트 중 오류 발생: {e}", font=("Arial", 12)).pack(pady=5)

def restart_application():
    python = sys.executable
    os.execl(python, python, *sys.argv)

if __name__ == "__main__":
    root = Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    # 전체 화면 설정
    root.attributes("-fullscreen", True)

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

    modbus_boxes = 7  # 원하는 Modbus TCP 상자 수를 설정하세요.
    analog_boxes = 7  # 원하는 4~20mA 상자 수를 설정하세요.

    main_frame = Frame(root)
    main_frame.grid(row=0, column=0)
    
    # 각 UI의 부모를 main_frame으로 설정
    modbus_ui = ModbusUI(main_frame, modbus_boxes)
    analog_ui = AnalogUI(main_frame, analog_boxes)

    modbus_ui.box_frame.grid(row=0, column=0, padx=10, pady=10)  # ModbusUI 배치
    analog_ui.box_frame.grid(row=1, column=0, padx=10, pady=10)  # AnalogUI 배치

    # 톱니바퀴 버튼 추가
    settings_button = Button(root, text="⚙", command=show_settings, font=("Arial", 20))
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
