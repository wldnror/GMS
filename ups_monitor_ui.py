from tkinter import Frame, Canvas

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

        # 상단 영역 (진한 회색)
        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(250 * SCALE_FACTOR), fill='#4B4B4B', outline='black', tags='border')
        # 하단 영역 (검정색)
        box_canvas.create_rectangle(0, int(310 * SCALE_FACTOR), int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR), fill='black', outline='black', tags='border')

        # 상시 모드 / 배터리 모드 표시
        box_canvas.create_text(int(80 * SCALE_FACTOR), int(30 * SCALE_FACTOR), text="UPS 모드", font=("Helvetica", int(14 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")
        self.mode_text_id = box_canvas.create_text(int(80 * SCALE_FACTOR), int(60 * SCALE_FACTOR), text="상시 모드", font=("Helvetica", int(12 * SCALE_FACTOR)), fill="#00AA00", anchor="center")  # 상시 모드 기본

        # 배터리 잔량 바
        box_canvas.create_rectangle(int(20 * SCALE_FACTOR), int(100 * SCALE_FACTOR), int(140 * SCALE_FACTOR), int(150 * SCALE_FACTOR), fill='white', outline='black')
        self.battery_level_bar = box_canvas.create_rectangle(int(20 * SCALE_FACTOR), int(100 * SCALE_FACTOR), int(20 * SCALE_FACTOR), int(150 * SCALE_FACTOR), fill='#00AA00', outline='')  # 잔량 초기값 0%

        # 잔량 퍼센트 텍스트
        self.battery_percentage_text = box_canvas.create_text(int(80 * SCALE_FACTOR), int(125 * SCALE_FACTOR), text="0%", font=("Helvetica", int(14 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")

        # UPS 및 제조사 정보
        box_canvas.create_text(int(80 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="UPS Monitor", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#FFFFFF", anchor="center")
        box_canvas.create_text(int(80 * SCALE_FACTOR), int(290 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD", font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#999999", anchor="center")

        self.box_frames.append((box_frame, box_canvas))

        # 예시로 배터리 상태를 업데이트하는 함수 호출 (실제 구현에서는 실제 배터리 상태를 받아서 업데이트)
        self.update_battery_status(box_canvas, battery_level=75, mode="배터리 모드")  # 예시로 75% 잔량, 배터리 모드로 설정

    def update_battery_status(self, canvas, battery_level, mode):
        """
        배터리 상태와 모드를 업데이트하는 함수
        :param canvas: 캔버스 객체
        :param battery_level: 배터리 잔량 (0 ~ 100)
        :param mode: 현재 UPS 모드 ("상시 모드" 또는 "배터리 모드")
        """
        # 배터리 잔량 바 업데이트
        battery_width = int(120 * SCALE_FACTOR * (battery_level / 100))  # 0% ~ 100%에 따라 바의 길이 조정
        canvas.coords(self.battery_level_bar, int(20 * SCALE_FACTOR), int(100 * SCALE_FACTOR), int(20 * SCALE_FACTOR) + battery_width, int(150 * SCALE_FACTOR))

        # 배터리 퍼센트 텍스트 업데이트
        canvas.itemconfig(self.battery_percentage_text, text=f"{battery_level}%")

        # UPS 모드 텍스트 및 색상 업데이트
        if mode == "상시 모드":
            canvas.itemconfig(self.mode_text_id, text="상시 모드", fill="#00AA00")
        else:
            canvas.itemconfig(self.mode_text_id, text="배터리 모드", fill="#AA0000")
