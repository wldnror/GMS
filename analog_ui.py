import os
import time
from tkinter import Frame, Canvas, StringVar, Toplevel
import threading
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import mplcursors
from common import SEGMENTS, create_segment_display

class AnalogUI:
    def __init__(self, root, num_boxes):
        self.root = root

        self.box_states = []
        self.histories = [[] for _ in range(num_boxes)]
        self.graph_windows = [None for _ in range(num_boxes)]
        self.history_window = None  # 히스토리 창을 저장할 변수
        self.history_lock = threading.Lock()  # 히스토리 창 중복 방지를 위한 락

        self.box_frame = Frame(self.root)
        self.box_frame.grid(row=0, column=0, padx=40, pady=40)

        self.row_frames = []
        self.box_frames = []
        self.history_dir = "analog_history_logs"

        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        for _ in range(num_boxes):
            self.create_analog_box()

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

    def create_analog_box(self):
        i = len(self.box_frames)
        row = i // 7
        col = i % 7

        if col == 0:
            row_frame = Frame(self.box_frame)
            row_frame.grid(row=row, column=0)
            self.row_frames.append(row_frame)
        else:
            row_frame = self.row_frames[-1]

        box_frame = Frame(row_frame)
        box_frame.grid(row=0, column=col, padx=20, pady=20)

        box_canvas = Canvas(box_frame, width=200, height=400, highlightthickness=4, highlightbackground="#000000", highlightcolor="#000000")
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, 210, 250, fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, 250, 210, 410, fill='black', outline='grey', tags='border')

        create_segment_display(box_canvas)
        self.box_states.append({
            "blink_state": False,
            "blinking_error": False,
            "previous_value_40011": None,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None
        })
        self.update_segment_display("    ", box_canvas, box_index=i)

        circle_items = []

        circle_items.append(box_canvas.create_oval(133, 200, 123, 190))
        box_canvas.create_text(95, 220, text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(77, 200, 87, 190))
        box_canvas.create_text(140, 220, text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(30, 200, 40, 190))
        box_canvas.create_text(35, 220, text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(171, 200, 181, 190))
        box_canvas.create_text(175, 213, text="FUT", fill="#cccccc", anchor="n")

        box_canvas.create_text(129, 105, text="ORG", font=("Helvetica", 18, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 360, text="GMS-1000", font=("Helvetica", 22, "bold"), fill="#cccccc", anchor="center")

        box_canvas.create_text(107, 395, text="GDS ENGINEERING CO.,LTD", font=("Helvetica", 9, "bold"), fill="#cccccc", anchor="center")

        self.box_frames.append((box_frame, box_canvas, circle_items, None, None, None))

        box_canvas.segment_canvas.bind("<Button-1>", lambda event, i=i: self.on_segment_click(i))

    def on_segment_click(self, box_index):
        threading.Thread(target=self.show_history_graph, args=(box_index,)).start()

    def update_circle_state(self, states, box_index=0):
        _, box_canvas, circle_items, _, _, _ = self.box_frames[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        for i, state in enumerate(states):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        if states[0]:
            outline_color = outline_colors[0]
        elif states[1]:
            outline_color = outline_colors[1]
        elif states[3]:
            outline_color = outline_colors[3]
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

    def update_segment_display(self, value, box_canvas, blink=False, box_index=0):
        value = value.zfill(4)
        leading_zero = True
        blink_state = self.box_states[box_index]["blink_state"]
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.record_history(box_index, value)
            self.box_states[box_index]["previous_segment_display"] = value

        for i, digit in enumerate(value):
            if leading_zero and digit == '0' and i < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS[digit]
                leading_zero = False

            if blink and blink_state:
                segments = SEGMENTS[' ']

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{i}_{chr(97 + j)}', fill=color)

        self.box_states[box_index]["blink_state"] = not blink_state

    def record_history(self, box_index, value):
        if value.strip():
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            log_line = f"{timestamp},{value}\n"
            log_file_prefix = os.path.join(self.history_dir, f"box_{box_index}")
            log_files = [f for f in os.listdir(self.history_dir) if f.startswith(f"box_{box_index}_")]
            log_files.sort()

            if log_files:
                latest_file = log_files[-1]
                with open(os.path.join(self.history_dir, latest_file), 'a') as file:
                    file.write(log_line)
                if sum(1 for line in open(os.path.join(self.history_dir, latest_file))) >= 10:
                    new_file = f"{log_file_prefix}_{len(log_files)}.log"
                    with open(new_file, 'a') as file:
                        file.write(log_line)
            else:
                new_file = f"{log_file_prefix}_0.log"
                with open(new_file, 'a') as file:
                    file.write(log_line)

    def show_history_graph(self, box_index):
        with self.history_lock:
            if self.history_window and self.history_window.winfo_exists():
                self.history_window.destroy()

            self.history_window = Toplevel(self.root)
            self.history_window.title(f"History - Box {box_index}")
            self.history_window.geometry("1200x800")
            self.history_window.attributes("-topmost", True)

            figure = plt.Figure(figsize=(12, 8), dpi=100)
            ax = figure.add_subplot(111)

            log_file_prefix = os.path.join(self.history_dir, f"box_{box_index}")
            log_files = [f for f in os.listdir(self.history_dir) if f.startswith(f"box_{box_index}_")]
            log_files.sort()

            times = []
            values = []
            for log_file in log_files:
                with open(os.path.join(self.history_dir, log_file), 'r') as file:
                    for line in file:
                        timestamp, value = line.strip().split(',')
                        times.append(timestamp)
                        values.append(value)

            ax.plot(times, values, marker='o')
            ax.set_title('History')
            ax.set_xlabel('Time')
            ax.set_ylabel('Value')
            figure.autofmt_xdate()

            canvas = FigureCanvasTkAgg(figure, master=self.history_window)
            canvas.draw()
            canvas.get_tk_widget().pack(side='top', fill='both', expand=1)

            mplcursors.cursor(ax)
