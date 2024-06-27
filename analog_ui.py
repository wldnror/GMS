# analog_ui.py

from tkinter import Tk
from common_ui import BaseUI

class AnalogUI(BaseUI):
    def __init__(self, root, num_boxes, gas_types, alarm_callback):
        super().__init__(root, num_boxes, gas_types, "analog_history_logs", alarm_callback)
        # AnalogUI 클래스의 추가 초기화 작업

# AnalogUI 클래스 사용 예시:
root = Tk()
app = AnalogUI(root, num_boxes=4, gas_types={"box_0": "ORG", "box_1": "ARF-T"}, alarm_callback=lambda active: print("Alarm active:", active))
root.mainloop()
