from tkinter import Tk, Frame
from modbus_ui import ModbusUI
from analog_ui import AnalogUI
import signal
import sys

if __name__ == "__main__":
    root = Tk()
    root.title("GDSENG - 스마트 모니터링 시스템")

    # 전체 화면 설정
    root.attributes("-fullscreen", True)

    # 기본 윈도우 크기 설정 (전체 화면을 사용하지 않는 경우)
    root.geometry("1920x1080")

    # 중앙 정렬 설정
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # ESC 키를 눌러 전체 화면 모드를 종료할 수 있도록 이벤트를 설정합니다.
    def exit_fullscreen(event):
        root.attributes("-fullscreen", False)

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
    main_frame.grid(row=0, column=0, sticky="nsew")
    main_frame.grid_rowconfigure(0, weight=1)
    main_frame.grid_rowconfigure(1, weight=1)
    main_frame.grid_columnconfigure(0, weight=1)

    # 각 UI의 부모를 main_frame으로 설정
    modbus_ui = ModbusUI(main_frame, modbus_boxes)
    analog_ui = AnalogUI(main_frame, analog_boxes)

    # 각 UI 요소의 크기를 조정
    modbus_ui.box_frame.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
    analog_ui.box_frame.grid(row=1, column=0, padx=20, pady=20, sticky="nsew")

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
