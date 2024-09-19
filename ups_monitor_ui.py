from tkinter import Frame, Canvas, Button

# 스케일 팩터로 20% 확대
SCALE_FACTOR = 1.65

class UPSMonitorUI:
    def __init__(self, parent, num_boxes):
        self.parent = parent
        self.box_frames = []
        self.box_data = []

        for i in range(num_boxes):
            self.create_ups_box(i)

    def create_ups_box(self, index):
        box_frame = Frame(self.parent, highlightthickness=int(5 * SCALE_FACTOR))

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=0, pady=0)

        box_canvas = Canvas(inner_frame, width=int(150 * SCALE_FACTOR), height=int(300 * SCALE_FACTOR), highlightthickness=int(3 * SCALE_FACTOR), highlightbackground="#000000", highlightcolor="#000000")
        box_canvas.pack()

        # 상단 영역 (진한 회색)
        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(250 * SCALE_FACTOR), fill='#4B4B4B', outline='black', tags='border')
        # 하단 영역 (검정색)
        box_canvas.create_rectangle(0, int(310 * SCALE_FACTOR), int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR), fill='black', outline='black', tags='border')

        # 배터리 모양으로 잔량 표시
        # 배터리 외곽 (두께를 두껍게 하고 왼쪽으로 이동)
        box_canvas.create_rectangle(int(15 * SCALE_FACTOR), int(20 * SCALE_FACTOR), int(135 * SCALE_FACTOR), int(60 * SCALE_FACTOR), fill='#4B4B4B', outline='black', width=int(3 * SCALE_FACTOR))
        # 배터리 양극 단자
        box_canvas.create_rectangle(int(135 * SCALE_FACTOR), int(30 * SCALE_FACTOR), int(145 * SCALE_FACTOR), int(50 * SCALE_FACTOR), fill='#4B4B4B', outline='black', width=int(2 * SCALE_FACTOR))
        # 배터리 잔량 바
        battery_level_bar = box_canvas.create_rectangle(int(20 * SCALE_FACTOR), int(25 * SCALE_FACTOR), int(20 * SCALE_FACTOR), int(55 * SCALE_FACTOR), fill='#00AA00', outline='')
        # 배터리 퍼센트 텍스트 (아이콘 내부 중앙에 배치)
        battery_percentage_text = box_canvas.create_text(int(75 * SCALE_FACTOR), int(40 * SCALE_FACTOR), text="0%", font=("Helvetica", int(12 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")

        # UPS 모드 표시
        mode_text_id = box_canvas.create_text(int(75 * SCALE_FACTOR), int(100 * SCALE_FACTOR), text="상시 모드", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#00FF00", anchor="center")

        # 모드 전환 버튼을 상자 내부에 배치
        toggle_button = Button(box_canvas, text="모드 전환", command=lambda: self.toggle_mode(box_canvas))
        box_canvas.create_window(int(75 * SCALE_FACTOR), int(140 * SCALE_FACTOR), window=toggle_button)

        # UPS 및 제조사 정보
        box_canvas.create_text(int(75 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="UPS Monitor", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")
        box_canvas.create_text(int(75 * SCALE_FACTOR), int(295 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD", font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#999999", anchor="center")

        # 필요한 데이터 저장
        self.box_frames.append(box_frame)
        self.box_data.append({
            "box_canvas": box_canvas,
            "battery_level_bar": battery_level_bar,
            "battery_percentage_text": battery_percentage_text,
            "mode_text_id": mode_text_id
        })

        # 초기 상태 업데이트 (상시 모드로 설정)
        self.update_battery_status(index, battery_level=self.calculate_battery_percentage(21.37), mode="상시 모드")  # 측정된 전압 예시 21.37V

    def update_battery_status(self, index, battery_level, mode):
        """
        배터리 상태와 모드를 업데이트하는 함수
        :param index: 박스 인덱스
        :param battery_level: 배터리 잔량 (0 ~ 100)
        :param mode: 현재 UPS 모드 ("상시 모드" 또는 "배터리 모드")
        """
        data = self.box_data[index]
        canvas = data["box_canvas"]
        battery_level_bar = data["battery_level_bar"]
        battery_percentage_text = data["battery_percentage_text"]
        mode_text_id = data["mode_text_id"]

        # 배터리 잔량 바 업데이트
        battery_width = int(110 * SCALE_FACTOR * (battery_level / 100))  # 0% ~ 100%에 따라 바의 길이 조정
        canvas.coords(battery_level_bar, int(20 * SCALE_FACTOR), int(25 * SCALE_FACTOR), int(20 * SCALE_FACTOR) + battery_width, int(55 * SCALE_FACTOR))

        # 배터리 퍼센트 텍스트 업데이트
        canvas.itemconfig(battery_percentage_text, text=f"{battery_level}%")

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
        mode_text_id = data["mode_text_id"]

        # 현재 모드 텍스트를 읽어와서 모드 전환
        current_mode = canvas.itemcget(mode_text_id, "text")
        new_mode = "배터리 모드" if current_mode == "상시 모드" else "상시 모드"

        # 상태 업데이트
        self.update_battery_status(index, battery_level=self.calculate_battery_percentage(21.37), mode=new_mode)  # 전압 예시로 21.37V

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
