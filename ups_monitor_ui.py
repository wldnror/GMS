from tkinter import Frame, Canvas

# 스케일 팩터로 20% 확대
SCALE_FACTOR = 1.65  

class UPSMonitorUI:
    def __init__(self, root, num_boxes):
        self.root = root
        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=10, pady=10)
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
            row_frame.grid(row=row, column=0, sticky="w", pady=5)
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame)
        box_frame.grid(row=0, column=col, padx=5)

        inner_frame = Frame(box_frame, highlightthickness=0)
        inner_frame.pack(padx=5, pady=5)

        box_canvas = Canvas(
            inner_frame,
            width=int(180 * SCALE_FACTOR),
            height=int(300 * SCALE_FACTOR),
            highlightthickness=0,
        )
        box_canvas.pack()

        # 외곽 상자 디자인
        box_canvas.create_rectangle(
            10, 10, int(150 * SCALE_FACTOR) - 10, int(300 * SCALE_FACTOR) - 10,
            fill='#F5F5F5', outline='#CCCCCC', tags='border'
        )

        # UPS 모드 텍스트
        box_canvas.create_text(
            int(75 * SCALE_FACTOR), int(40 * SCALE_FACTOR),
            text="UPS 모드", font=("Helvetica", int(14 * SCALE_FACTOR), "bold"), 
            fill="#333333", anchor="center"
        )
        self.mode_text_id = box_canvas.create_text(
            int(75 * SCALE_FACTOR), int(70 * SCALE_FACTOR), 
            text="상시 모드", font=("Helvetica", int(12 * SCALE_FACTOR)), 
            fill="#00AA00", anchor="center"
        )

        # 배터리 잔량 바
        box_canvas.create_rectangle(
            int(20 * SCALE_FACTOR), int(120 * SCALE_FACTOR), 
            int(130 * SCALE_FACTOR), int(170 * SCALE_FACTOR), 
            fill='white', outline='#AAAAAA'
        )
        self.battery_level_bar = box_canvas.create_rectangle(
            int(20 * SCALE_FACTOR), int(120 * SCALE_FACTOR), 
            int(20 * SCALE_FACTOR), int(170 * SCALE_FACTOR), 
            fill='#00AA00', outline=''
        )

        # 잔량 퍼센트 텍스트
        self.battery_percentage_text = box_canvas.create_text(
            int(75 * SCALE_FACTOR), int(145 * SCALE_FACTOR), 
            text="0%", font=("Helvetica", int(14 * SCALE_FACTOR), "bold"), 
            fill="#333333", anchor="center"
        )

        # UPS 및 제조사 정보
        box_canvas.create_text(
            int(75 * SCALE_FACTOR), int(230 * SCALE_FACTOR), 
            text="UPS Monitor", font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), 
            fill="#333333", anchor="center"
        )
        box_canvas.create_text(
            int(75 * SCALE_FACTOR), int(260 * SCALE_FACTOR), 
            text="GDS ENGINEERING CO.,LTD", font=("Helvetica", int(8 * SCALE_FACTOR)), 
            fill="#666666", anchor="center"
        )

        self.box_frames.append((box_frame, box_canvas))

        # 예시로 배터리 상태를 업데이트하는 함수 호출
        self.update_battery_status(box_canvas, battery_level=75, mode="배터리 모드")

    def update_battery_status(self, canvas, battery_level, mode):
        """
        배터리 상태와 모드를 업데이트하는 함수
        :param canvas: 캔버스 객체
        :param battery_level: 배터리 잔량 (0 ~ 100)
        :param mode: 현재 UPS 모드 ("상시 모드" 또는 "배터리 모드")
        """
        # 배터리 잔량 바 업데이트
        battery_width = int(110 * SCALE_FACTOR * (battery_level / 100))
        canvas.coords(
            self.battery_level_bar, 
            int(20 * SCALE_FACTOR), int(120 * SCALE_FACTOR), 
            int(20 * SCALE_FACTOR) + battery_width, int(170 * SCALE_FACTOR)
        )

        # 배터리 퍼센트 텍스트 업데이트
        canvas.itemconfig(self.battery_percentage_text, text=f"{battery_level}%")

        # UPS 모드 텍스트 및 색상 업데이트
        if mode == "상시 모드":
            canvas.itemconfig(self.mode_text_id, text="상시 모드", fill="#00AA00")
        else:
            canvas.itemconfig(self.mode_text_id, text="배터리 모드", fill="#AA0000")
