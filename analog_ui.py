from tkinter import Frame, Canvas
from common import SEGMENTS, create_segment_display, show_history_graph
import sqlite3
import time

def initialize_database():
    conn = sqlite3.connect('histories.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            box_index INTEGER,
            timestamp TEXT,
            value TEXT,
            last_value TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_history_to_db(box_index, timestamp, value, last_value):
    conn = sqlite3.connect('histories.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO history (box_index, timestamp, value, last_value)
        VALUES (?, ?, ?, ?)
    ''', (box_index, timestamp, value, last_value))
    conn.commit()
    conn.close()

def load_history_from_db(box_index):
    conn = sqlite3.connect('histories.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT timestamp, value, last_value
        FROM history
        WHERE box_index = ?
        ORDER BY timestamp DESC
        LIMIT 100
    ''', (box_index,))
    histories = cursor.fetchall()
    conn.close()
    return histories

class AnalogUI:
    def __init__(self, root, num_boxes):
        self.root = root
        initialize_database()  # 데이터베이스 초기화 호출
        self.histories = [[] for _ in range(num_boxes)]  # 히스토리 저장을 위한 리스트 초기화
        self.graph_windows = [None for _ in range(num_boxes)]  # 그래프 윈도우 저장을 위한 리스트 초기화
        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=40, pady=40)  # padding 증가

        self.row_frames = []  # 각 행의 프레임을 저장할 리스트
        self.box_frames = []  # UI 상자를 저장할 리스트

        for _ in range(num_boxes):
            self.create_analog_box()

        # 모든 동그라미를 꺼는 초기화
        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

    def create_analog_box(self):
        i = len(self.box_frames)
        row = i // 7
        col = i % 7

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0)  # grid로 변경
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame)
        box_frame.grid(row=0, column=col, padx=20, pady=20)  # padding 증가

        box_canvas = Canvas(box_frame, width=200, height=400, highlightthickness=4, highlightbackground="#000000",
                            highlightcolor="#000000")
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, 210, 250, fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, 250, 210, 410, fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)  # 세그먼트 디스플레이 생성
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,  # 이전 세그먼트 값 저장
            "last_history_time": None,  # 마지막 히스토리 기록 시간
            "last_history_value": None  # 마지막 히스토리 기록 값
        })
        self.update_segment_display("    ", box_canvas, box_index=i)  # 초기화시 빈 상태로 설정

        # 동그라미 상태를 저장할 리스트
        circle_items = []

        # Draw small circles in the desired positions (moved to gray section)
        # Left vertical row under the segment display
        circle_items.append(
            box_canvas.create_oval(133, 200, 123, 190))  # Red circle 1
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(
            box_canvas.create_oval(77, 200, 87, 190))  # Red circle 2
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(
            box_canvas.create_oval(30, 200, 40, 190))  # Green circle 1
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        # Right horizontal row under the segment display
        circle_items.append(
            box_canvas.create_oval(171, 200, 181, 190))  # Yellow circle 1
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        # 상자 세그먼트 아래에 "가스명" 글자 추가
        box_canvas.create_text(129, 105, text="ORG", font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")

        # 상자 맨 아래에 "GDS SMS" 글자 추가
        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc",
                               anchor="center")

        # 상자 맨 아래에 "GDS ENGINEERING CO.,LTD" 글자 추가
        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc",
                               anchor="center")

        # 4~20mA 상자는 bar 관련 UI 요소를 추가하지 않음
        self.box_frames.append((box_frame, box_canvas, circle_items, None, None, None))

        # 세그먼트 클릭 시 히스토리를 그래프로 보여주는 이벤트 추가
        box_canvas.bind("<Button-1>", lambda event, i=i: self.show_history(i))

    def show_history(self, box_index):
        history = load_history_from_db(box_index)
        self.histories[box_index] = history
        show_history_graph(self.root, box_index, self.histories, self.graph_windows)

    def update_circle_state(self, states, box_index=0):
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        if states[0]:  # Red top-left
            outline_color = outline_colors[0]
        elif states[1]:  # Red top-right
            outline_color = outline_colors[1]
        elif states[3]:  # Yellow bottom-right
            outline_color = outline_colors[3]
        else:  # Default grey outline
            outline_color = outline_color_off

        # 박스 테두리 업데이트
        box_canvas.config(highlightbackground=outline_color)

    def update_segment_display(self, value, box_canvas, blink=False, box_index=0):
        value = value.zfill(4)  # Ensure the value is 4 characters long, padded with zeros if necessary
        leading_zero = True
        blink_state = self.box_states[box_index]["blink_state"]
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:  # 값이 변했을 때만 기록
            self.record_history(box_index, value)
            self.box_states[box_index]["previous_segment_display"] = value

        for i, digit in enumerate(value):
            if leading_zero and digit == '0' and i < 3:
                # 앞의 세 자릿수가 0이면 회색으로 설정
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS[digit]
                leading_zero = False

            if blink and blink_state:
                segments = SEGMENTS[' ']  # 깜빡임 상태에서는 모든 세그먼트를 끕니다.

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.itemconfig(f'segment_{i}_{chr(97 + j)}', fill=color)

        self.box_states[box_index]["blink_state"] = not blink_state  # 깜빡임 상태 토글

    def record_history(self, box_index, value):
        if value.strip():  # 값이 공백이 아닌 경우에만 기록
            last_history_value = self.box_states[box_index]["last_history_value"]
            if value != last_history_value:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                last_value = self.box_states[box_index].get("last_value_40005", 0)
                save_history_to_db(box_index, timestamp, value, last_value)
                self.box_states[box_index]["last_history_value"] = value
