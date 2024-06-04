from tkinter import Tk
from modbus_ui import ModbusUI
from analog_ui import AnalogUI

if __name__ == "__main__":
    root = Tk()
    modbus_boxes = 5  # 원하는 Modbus TCP 상자 수를 설정하세요.
    analog_boxes = 6  # 원하는 4~20mA 상자 수를 설정하세요.

    modbus_ui = ModbusUI(root, modbus_boxes)
    analog_ui = AnalogUI(root, analog_boxes)

    root.mainloop()

    for _, client in modbus_ui.clients.items():
        client.close()
