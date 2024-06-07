from tkinter import Tk, Frame, Label
import signal
import sys

class ModbusUI(Frame):
    def __init__(self, parent, boxes):
        super().__init__(parent)
        self.pack(expand=True, fill='both')
        self.clients = {}
        for i in range(boxes):
            label = Label(self, text=f'Modbus Box {i+1}')
            label.place(relx=0.5, rely=0.1*(i+1), anchor='center')

class AnalogUI(Frame):
    def __init__(self, parent, boxes):
        super().__init__(parent)
        self.pack(expand=True, fill='both')
        for i in range(boxes):
            label = Label(self, text=f'Analog Box {i+1}')
            label.place(relx=0.5, rely=0.1*(i+1+5), anchor='center')  # Adjust y-position as needed

if __name__ == "__main__":
    root = Tk()

    # 전체 화면 설정
    root.attributes("-fullscreen", True)

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

    modbus_boxes = 5  # 원하는 Modbus TCP 상자 수를 설정하세요.
    analog_boxes = 6  # 원하는 4~20mA 상자 수를 설정하세요.

    modbus_ui = ModbusUI(root, modbus_boxes)
    analog_ui = AnalogUI(root, analog_boxes)

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
