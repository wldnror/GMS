from tkinter import Frame, Canvas, Button
import time
import threading

# Adafruit INA219 라이브러리 임포트
from adafruit_ina219 import INA219
from busio import I2C
import board

# 배터리 보정값 (0 ~ 100 범위로 설정)
BATTERY_ADJUSTMENT = 22  # 여기서 값을 수정하여 보정

# 스케일 팩터로 20% 확대
SCALE_FACTOR = 1.65


class UPSMonitorUI:
    def __init__(self, parent, num_boxes):
        self.parent = parent
        self.box_frames = []
        self.box_data = []
        self.ina219_available = False  # INA219 사용 가능 여부 플래그 추가
        self.last_battery_level = 0
        self.last_voltage = 0.0

        # I2C 통신 설정
        i2c_bus = I2C(board.SCL, board.SDA)

        # INA219 인스턴스 생성 시도
        try:
            self.ina219 = INA219(i2c_bus)
            self.ina219_available = True
            print("INA219 센서가 성공적으로 초기화되었습니다.")
        except ValueError as e:
            print(f"INA219 센서를 찾을 수 없습니다: {e}")
            self.ina219_available = False

        for i in range(num_boxes):
            self.create_ups_box(i)

        # 주기적으로 업데이트하는 쓰레드 시작
        self.running = True
        self.update_thread = threading.Thread(target=self.update_loop)
        self.update_thread.start()

    def create_ups_box(self, index):
        box_frame = Frame(self.parent, highlightthickness=int(7 * SCALE_FACTOR))

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(inner_frame, width=int(150 * SCALE_FACTOR), height=int(300 * SCALE_FACTOR),
                            highlightthickness=int(3 * SCALE_FACTOR), highlightbackground="#000000",
                            highlightcolor="#000000")
        box_canvas.pack()

        # 상단 영역 (진한 회색)
        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(250 * SCALE_FACTOR), fill='#4B4B4B',
                                     outline='black', tags='border')
        # 하단 영역 (검정색)
        box_canvas.create_rectangle(0, int(310 * SCALE_FACTOR), int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR),
                                     fill='black', outline='black', tags='border')

        # 배터리 모양으로 잔량 표시
        # 배터리 외곽
        box_canvas.create_rectangle(int(15 * SCALE_FACTOR), int(20 * SCALE_FACTOR), int(135 * SCALE_FACTOR),
                                     int(60 * SCALE_FACTOR), fill='#4B4B4B', outline='black',
                                     width=int(3 * SCALE_FACTOR))
        # 배터리 양극 단자
        box_canvas.create_rectangle(int(135 * SCALE_FACTOR), int(30 * SCALE_FACTOR), int(145 * SCALE_FACTOR),
                                     int(50 * SCALE_FACTOR), fill='#4B4B4B', outline='black',
                                     width=int(2 * SCALE_FACTOR))
        # 배터리 잔량 바
        battery_level_bar = box_canvas.create_rectangle(int(20 * SCALE_FACTOR), int(25 * SCALE_FACTOR),
                                                        int(20 * SCALE_FACTOR), int(55 * SCALE_FACTOR),
                                                        fill='#00AA00', outline='')
        # 배터리 퍼센트 텍스트
        battery_percentage_text = box_canvas.create_text(int(75 * SCALE_FACTOR), int(40 * SCALE_FACTOR), text="0%",
                                                         font=("Helvetica", int(12 * SCALE_FACTOR), "bold"),
                                                         fill="#FFFFFF", anchor="center")

        # 전압 표시 텍스트 추가 (예쁘게 가운데 정렬, 하얀색)
        voltage_text_id = box_canvas.create_text(int(75 * SCALE_FACTOR), int(80 * SCALE_FACTOR), text="0.00V",
                                                 font=("Helvetica", int(12 * SCALE_FACTOR), "bold"),
                                                 fill="#FFFFFF", anchor="center")

        # UPS 모드 표시
        mode_text_id = box_canvas.create_text(int(75 * SCALE_FACTOR), int(100 * SCALE_FACTOR), text="상시 모드",
                                              font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#00FF00",
                                              anchor="center")

        # 모드 전환 버튼
        toggle_button = Button(box_canvas, text="모드 전환", command=lambda: self.toggle_mode(box_canvas))
        box_canvas.create_window(int(75 * SCALE_FACTOR), int(140 * SCALE_FACTOR), window=toggle_button)

        # UPS 및 제조사 정보
        box_canvas.create_text(int(75 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="UPS Monitor",
                               font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")
        box_canvas.create_text(int(75 * SCALE_FACTOR), int(295 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD",
                               font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#999999", anchor="center")

        # 필요한 데이터 저장
        self.box_frames.append(box_frame)
        self.box_data.append({
            "box_canvas": box_canvas,
            "battery_level_bar": battery_level_bar,
            "battery_percentage_text": battery_percentage_text,
            "mode_text_id": mode_text_id,
            "mode": "상시 모드",  # 초기 모드 설정
            "voltage_text_id": voltage_text_id
        })

        # 프레임을 부모 위젯에 추가
        box_frame.pack(side="left", padx=10, pady=10)

    def update_battery_status(self, index, battery_level, mode, voltage=0.0):
        """
        배터리 상태, 모드, 전압을 업데이트하는 함수
        :param index: 박스 인덱스
        :param battery_level: 배터리 잔량 (0 ~ 100)
        :param mode: 현재 UPS 모드 ("상시 모드" 또는 "배터리 모드")
        :param voltage: 현재 측정된 전압 값
        """
        # 배터리 레벨에 보정값 추가
        adjusted_battery_level = min(max(battery_level + BATTERY_ADJUSTMENT, 0), 100)  # 0~100 범위 제한
        data = self.box_data[index]
        canvas = data["box_canvas"]
        battery_level_bar = data["battery_level_bar"]
        battery_percentage_text = data["battery_percentage_text"]
        mode_text_id = data["mode_text_id"]
        voltage_text_id = data["voltage_text_id"]

        if self.ina219_available:
            # 배터리 잔량 바 업데이트
            battery_width = int(110 * SCALE_FACTOR * (adjusted_battery_level / 100))  # 0% ~ 100%에 따라 바의 길이 조정
            canvas.coords(battery_level_bar, int(20 * SCALE_FACTOR), int(25 * SCALE_FACTOR),
                          int(20 * SCALE_FACTOR) + battery_width, int(55 * SCALE_FACTOR))

            # 배터리 퍼센트 텍스트 업데이트
            canvas.itemconfig(battery_percentage_text, text=f"{adjusted_battery_level}%")

            # 전압 텍스트 업데이트 (소수점 둘째자리까지 표시)
            canvas.itemconfig(voltage_text_id, text=f"{voltage:.2f}V")
        else:
            # 센서가 없을 경우, 배터리 잔량 바를 비우고 텍스트를 "연결되지 않음"으로 표시
            canvas.coords(battery_level_bar, int(20 * SCALE_FACTOR), int(25 * SCALE_FACTOR),
                          int(20 * SCALE_FACTOR), int(55 * SCALE_FACTOR))
            canvas.itemconfig(battery_percentage_text, text="연결되지 않음")
            canvas.itemconfig(voltage_text_id, text="N/A")

        # UPS 모드 텍스트 및 색상 업데이트
        if mode == "상시 모드":
            canvas.itemconfig(mode_text_id, text="상시 모드", fill="#00AA00")
        else:
            canvas.itemconfig(mode_text_id, text="배터리 모드", fill="#AA0000")

    def toggle_mode(self, canvas):
        """
        모드를 전환하는 함수 (상시 모드 <-> 배터리 모드)
        """
        # 해당 박스의 인덱스를 찾습니다.
        index = None
        for i, data in enumerate(self.box_data):
            if data["box_canvas"] == canvas:
                index = i
                break

        if index is None:
            return

        data = self.box_data[index]

        # 현재 모드 전환
        current_mode = data["mode"]
        new_mode = "배터리 모드" if current_mode == "상시 모드" else "상시 모드"
        data["mode"] = new_mode

        # 상태 업데이트 (배터리 레벨, 전압은 마지막 측정값 사용)
        self.update_battery_status(index, battery_level=self.last_battery_level, mode=new_mode, voltage=self.last_voltage)

    def calculate_battery_percentage(self, voltage):
        """
        배터리 전압을 잔량 퍼센트로 변환하는 함수
        :param voltage: 측정된 전체 전압
        :return: 잔량 퍼센트 (0 ~ 100)
        """
        cell_voltage = voltage / 6  # 6셀 배터리로 가정
        # 리튬 배터리 전압에 따른 대략적인 잔량 계산
        if cell_voltage >= 4.2:
            return 100
        elif cell_voltage > 3.7:
            return int((cell_voltage - 3.7) / (4.2 - 3.7) * 50 + 50)
        elif cell_voltage > 3.0:
            return int((cell_voltage - 3.0) / (3.7 - 3.0) * 50)
        else:
            return 0

    def update_loop(self):
        """
        주기적으로 배터리 상태를 업데이트하는 함수
        """
        while self.running:
            try:
                if self.ina219_available:
                    # INA219에서 전압 측정
                    bus_voltage = self.ina219.bus_voltage  # 전압 (V)
                    shunt_voltage = self.ina219.shunt_voltage  # 션트 전압 (mV)
                    voltage = bus_voltage + (shunt_voltage / 1000)  # 전체 전압 계산

                    # 배터리 잔량 계산
                    battery_level = self.calculate_battery_percentage(voltage)
                    self.last_battery_level = battery_level
                    self.last_voltage = voltage
                else:
                    # 센서가 없을 경우
                    battery_level = 0
                    voltage = 0.0
                    self.last_battery_level = battery_level
                    self.last_voltage = voltage

                # 각 박스에 대해 업데이트
                for index, data in enumerate(self.box_data):
                    self.update_battery_status(index, battery_level=battery_level, mode=data["mode"], voltage=voltage)

                # 1초마다 업데이트
                time.sleep(1)
            except Exception as e:
                print(f"업데이트 중 오류 발생: {e}")
                time.sleep(1)

    def stop(self):
        """
        쓰레드를 중지하는 함수
        """
        self.running = False
        self.update_thread.join()
