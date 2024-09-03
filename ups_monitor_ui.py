from tkinter import Frame, Canvas, Button, Tk

# 스케일 팩터로 20% 확대
SCALE_FACTOR = 1.65

class UPSMonitorUI:
    def __init__(self, root, num_boxes):
        self.root = root
        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0)
        self.row_frames = []
        self.box_frames = []

        for i in range(num_boxes):
            self.create_ups_box(i)

    def create_ups_box(self, index):
        max_boxes_per_row = 6
        row = index // max_boxes_per_row
        col = index % max_boxes_per_row

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0, sticky="w")
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame, highlightthickness=int(2.5 * SCALE_FACTOR))
        box_frame.grid(row=0, column=col)

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=int(2.5 * SCALE_FACTOR), pady=int(2.5 * SCALE_FACTOR))

        box_canvas = Canvas(inner_frame, width=int(150 * SCALE_FACTOR), height=int(300 * SCALE_FACTOR), highlightthickness=int(3 * SCALE_FACTOR), highlightbackground="#000000", highlightcolor="#000000")
        box_canvas.pack()

        # 배터리 모양 그리기
        self.draw_battery_shape(box_canvas)

        # 잔량 바와 퍼센트 텍스트 초기화
        self.battery_level_bar = box_canvas.create_rectangle(int(22 * SCALE_FACTOR), int(72 * SCALE_FACTOR), int(138 * SCALE_FACTOR), int(230 * SCALE_FACTOR), fill='#00AA00', outline='')
        self.battery_percentage_text = box_canvas.create_text(int(80 * SCALE_FACTOR), int(50 * SCALE_FACTOR), text="0%", font=("Helvetica", int(14 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")

        # UPS 모드 표시
        self.mode_text_id = box_canvas.create_text(int(80 * SCALE_FACTOR), int(100 * SCALE_FACTOR), text="상시 모드", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#00FF00", anchor="center")

        # 모드 전환 버튼을 상자 내부에 배치
        toggle_button = Button(inner_frame, text="모드 전환", command=lambda: self.toggle_mode(box_canvas))
        toggle_button.pack(pady=int(5 * SCALE_FACTOR))

        # UPS 및 제조사 정보
        box_canvas.create_text(int(80 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="UPS Monitor", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")
        box_canvas.create_text(int(80 * SCALE_FACTOR), int(295 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD", font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#999999", anchor="center")

        self.box_frames.append((box_frame, box_canvas))

        # 초기 상태 업데이트 (상시 모드로 설정)
        self.update_battery_status(box_canvas, battery_level=self.calculate_battery_percentage(21.37), mode="상시 모드")  # 측정된 전압 예시 21.37V

    def draw_battery_shape(self, canvas):
        """
        배터리 모양을 그리는 함수
        """
        # 배터리 몸체 (회색)
        canvas.create_rectangle(int(20 * SCALE_FACTOR), int(70 * SCALE_FACTOR), int(140 * SCALE_FACTOR), int(230 * SCALE_FACTOR), fill='#4B4B4B', outline='black', tags='border')
        # 배터리 헤드 (검정색, 위쪽 작은 사각형)
        canvas.create_rectangle(int(60 * SCALE_FACTOR), int(60 * SCALE_FACTOR), int(100 * SCALE_FACTOR), int(70 * SCALE_FACTOR), fill='black', outline='black')

    def update_battery_status(self, canvas, battery_level, mode):
        """
        배터리 상태와 모드를 업데이트하는 함수
        :param canvas: 캔버스 객체
        :param battery_level: 배터리 잔량 (0 ~ 100)
        :param mode: 현재 UPS 모드 ("상시 모드" 또는 "배터리 모드")
        """
        # 배터리 잔량 바 업데이트
        fill_height = int(72 * SCALE_FACTOR + (158 * SCALE_FACTOR * (battery_level / 100)))  # 배터리 내부 바의 높이 조정
        fill_color = self.get_battery_color(battery_level)  # 잔량에 따른 색상 결정
        canvas.coords(self.battery_level_bar, int(22 * SCALE_FACTOR), fill_height, int(138 * SCALE_FACTOR), int(230 * SCALE_FACTOR))
        canvas.itemconfig(self.battery_level_bar, fill=fill_color)

        # 배터리 퍼센트 텍스트 업데이트
        canvas.itemconfig(self.battery_percentage_text, text=f"{battery_level}%")

        # UPS 모드 텍스트 및 색상 업데이트
        if mode == "상시 모드":
            canvas.itemconfig(self.mode_text_id, text="상시 모드", fill="#00AA00")
        else:
            canvas.itemconfig(self.mode_text_id, text="배터리 모드", fill="#AA0000")

    def toggle_mode(self, canvas):
        """
        모드를 전환하는 함수 (상시 모드 <-> 배터리 모드)
        """
        # 현재 모드 텍스트를 읽어와서 모드 전환
        current_mode = canvas.itemcget(self.mode_text_id, "text")
        new_mode = "배터리 모드" if current_mode == "상시 모드" else "상시 모드"

        # 상태 업데이트
        self.update_battery_status(canvas, battery_level=self.calculate_battery_percentage(21.37), mode=new_mode)  # 전압 예시로 21.37V

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

    def get_battery_color(self, battery_level):
        """
        배터리 잔량에 따라 색상을 결정하는 함수
        :param battery_level: 배터리 잔량 (0 ~ 100)
        :return: 색상 코드
        """
        if battery_level > 50:
            # 50% 이상일 때 초록색으로 점점 밝아짐
            red = int(255 * (1 - (battery_level - 50) / 50))
            green = 255
            blue = 0
        else:
            # 50% 이하일 때 빨간색에서 노란색으로 변함
            red = 255
            green = int(255 * (battery_level / 50))
            blue = 0
        return f'#{red:02x}{green:02x}{blue:02x}'

if __name__ == "__main__":
    root = Tk()
    app = UPSMonitorUI(root, num_boxes=8)  # 예시로 8개의 박스 생성
    root.mainloop()
