from tkinter import Canvas, Toplevel
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

# 세그먼트 표시 매핑
SEGMENTS = {
    '0': '1111110',
    '1': '0110000',
    '2': '1101101',
    '3': '1111001',
    '4': '0110011',
    '5': '1011011',
    '6': '1011111',
    '7': '1110000',
    '8': '1111111',
    '9': '1111011',
    'E': '1001111',  # a, f, e, g, d
    '-': '0000001',  # g
    ' ': '0000000'  # 모든 세그먼트 꺼짐
}

# Bit to segment mapping
BIT_TO_SEGMENT = {
    0: 'E-10',  # E-10
    1: 'E-22',  # E-22
    2: 'E-12',  # E-12
    3: 'E-23'  # E-23
}

# 확대 배율
SCALE = 1.2
# 위치 이동 값
x_shift = 0
y_shift = -10

def create_gradient_bar(width, height):
    gradient = Image.new('RGB', (width, height), color=0)
    for i in range(width):
        ratio = i / width
        if ratio < 0.25:
            r = int(0 + (255 * ratio * 4))
            g = 255
            b = 0
        elif ratio < 0.5:
            r = 255
            g = int(255 - (255 * (ratio - 0.25) * 4))
            b = 0
        elif ratio < 0.75:
            r = 255
            g = 0
            b = int(255 * (ratio - 0.5) * 4)
        else:
            r = int(255 - (255 * (ratio - 0.75) * 4))
            g = 0
            b = 255

        for j in range(height):
            gradient.putpixel((i, j), (r, g, b))

    return gradient

def create_segment_display(box_canvas):
    segment_canvas = Canvas(box_canvas, width=(131 + x_shift) * SCALE, height=(60 + y_shift) * SCALE, bg='#000000', highlightthickness=0)
    segment_canvas.place(x=(23 + x_shift) * SCALE, y=(24 + y_shift) * SCALE)  # 전체 위치 이동 적용

    segment_items = []
    for i in range(4):
        x_offset = (i * 29 + 14) * SCALE  # x축 위치는 각 세그먼트에 맞게 조정
        y_offset = 0  # y축 위치는 동일하게 유지
        segments = [
            # 상단 (4만큼 아래로 이동, 두께 10% 감소)
            segment_canvas.create_polygon(4 * SCALE + x_offset, 11.2 * SCALE + y_offset, 12 * SCALE + x_offset, 11.2 * SCALE + y_offset, 16 * SCALE + x_offset, 13.6 * SCALE + y_offset,
                                          12 * SCALE + x_offset, 16 * SCALE + y_offset, 4 * SCALE + x_offset, 16 * SCALE + y_offset, 0 * SCALE + x_offset, 13.6 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_a'),

            # 상단-오른쪽 (세로 열, 두께 감소, 3만큼 아래로 이동)
            segment_canvas.create_polygon(16 * SCALE + x_offset, 15 * SCALE + y_offset, 17.6 * SCALE + x_offset, 17.4 * SCALE + y_offset, 17.6 * SCALE + x_offset, 27.4 * SCALE + y_offset,
                                          16 * SCALE + x_offset, 29.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 27.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 17.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_b'),

            # 하단-오른쪽 (세로 열, 두께 감소, 1만큼 위로 이동)
            segment_canvas.create_polygon(16 * SCALE + x_offset, 31 * SCALE + y_offset, 17.6 * SCALE + x_offset, 33.4 * SCALE + y_offset, 17.6 * SCALE + x_offset, 43.4 * SCALE + y_offset,
                                          16 * SCALE + x_offset, 45.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 43.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 33.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_c'),

            # 하단 (7만큼 위로 이동, 두께 10% 감소)
            segment_canvas.create_polygon(4 * SCALE + x_offset, 43.8 * SCALE + y_offset, 12 * SCALE + x_offset, 43.8 * SCALE + y_offset, 16 * SCALE + x_offset, 46.2 * SCALE + y_offset,
                                          12 * SCALE + x_offset, 48.6 * SCALE + y_offset, 4 * SCALE + x_offset, 48.6 * SCALE + y_offset, 0 * SCALE + x_offset, 46.2 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_d'),

            # 하단-왼쪽 (세로 열, 두께 감소, 1만큼 위로 이동)
            segment_canvas.create_polygon(0 * SCALE + x_offset, 31 * SCALE + y_offset, 1.6 * SCALE + x_offset, 33.4 * SCALE + y_offset, 1.6 * SCALE + x_offset, 43.4 * SCALE + y_offset,
                                          0 * SCALE + x_offset, 45.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 43.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 33.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_e'),

            # 상단-왼쪽 (세로 열, 두께 감소, 3만큼 아래로 이동)
            segment_canvas.create_polygon(0 * SCALE + x_offset, 15 * SCALE + y_offset, 1.6 * SCALE + x_offset, 17.4 * SCALE + y_offset, 1.6 * SCALE + x_offset, 27.4 * SCALE + y_offset,
                                          0 * SCALE + x_offset, 29.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 27.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 17.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_f'),

            # 중간 (두께 10% 감소, 아래로 8만큼 이동)
            segment_canvas.create_polygon(4 * SCALE + x_offset, 27.8 * SCALE + y_offset, 12 * SCALE + x_offset, 27.8 * SCALE + y_offset, 16 * SCALE + x_offset, 30.2 * SCALE + y_offset,
                                          12 * SCALE + x_offset, 32.6 * SCALE + y_offset, 4 * SCALE + x_offset, 32.6 * SCALE + y_offset, 0 * SCALE + x_offset, 30.2 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_g')
        ]
        segment_items.append(segments)

    box_canvas.segment_canvas = segment_canvas
    box_canvas.segment_items = segment_items

def show_history_graph(root, box_index, histories, graph_windows):
    if graph_windows[box_index] is not None:
        return  # 그래프 창이 이미 열려 있으면 새로 열지 않음

    graph_window = Toplevel(root)
    graph_window.title(f"Box {box_index + 1} Segment Value History")

    fig, ax = plt.subplots(figsize=(10, 5))
    graph_windows[box_index] = graph_window
    canvas = FigureCanvasTkAgg(fig, master=graph_window)
    canvas.get_tk_widget().pack(side="top", fill="both", expand=1)
    canvas.draw()

    def on_close():
        graph_windows[box_index] = None
        graph_window.destroy()

    graph_window.protocol("WM_DELETE_WINDOW", on_close)

    # 주기적으로 그래프를 업데이트하는 함수 호출
    def periodic_update():
        if graph_windows[box_index] is not None:
            update_graph(box_index, ax, histories)
            canvas.draw()
            graph_window.after(100, periodic_update)

    periodic_update()

def update_graph(box_index, ax, histories):
    timestamps = [record[0] for record in histories[box_index]]
    values = []
    labels = []
    errors = {'E-10': [], 'E-22': [], 'E-12': [], 'E-23': []}
    alarms = {'A1': [], 'A2': []}
    disconnects = []

    for record in histories[box_index]:
        try:
            value = int(record[1])
            values.append(value)
            labels.append('')
        except ValueError:
            if record[1] in errors:
                errors[record[1]].append((record[0], record[2]))
            elif record[1] in alarms:
                alarms[record[1]].append((record[0], record[2]))
            else:
                values.append(0)
                labels.append('')

    # Ensure timestamps and values have the same length
    min_length = min(len(timestamps), len(values))
    timestamps = timestamps[:min_length]
    values = values[:min_length]
    labels = labels[:min_length]

    ax.clear()
    line, = ax.plot(timestamps, values, marker='o')
    ax.set_xlabel('Timestamp')
    ax.set_ylabel('Value')
    ax.set_title(f'Box {box_index + 1} Segment Value History')
    ax.tick_params(axis='x', rotation=45)
    ax.figure.tight_layout()

    annot = ax.annotate("", xy=(0, 0), xytext=(20, 20),
                        textcoords="offset points",
                        bbox=dict(boxstyle="round", fc="w"),
                        arrowprops=dict(arrowstyle="->"))
    annot.set_visible(False)

    def update_annot(ind):
        x, y = line.get_data()
        annot.xy = (x[ind["ind"][0]], y[ind["ind"][0]])
        text = f'Time: {timestamps[ind["ind"][0]]}\nValue: {values[ind["ind"][0]]}'
        annot.set_text(text)
        annot.get_bbox_patch().set_alpha(0.6)

    def on_hover(event):
        vis = annot.get_visible()
        if event.inaxes == ax:
            cont, ind = line.contains(event)
            if cont:
                update_annot(ind)
                annot.set_visible(True)
                ax.figure.canvas.draw_idle()
            else:
                if vis:
                    annot.set_visible(False)
                    ax.figure.canvas.draw_idle()

    ax.figure.canvas.mpl_connect("motion_notify_event", on_hover)

    # 에러와 알람 시각적 표시
    for error, points in errors.items():
        for time, value in points:
            if time in timestamps:
                idx = timestamps.index(time)
                ax.scatter(timestamps[idx], values[idx], color='red', label=error, zorder=5)
                ax.annotate(error, (timestamps[idx], values[idx]), textcoords="offset points", xytext=(0, 10),
                            ha='center', color='red')

    for alarm, points in alarms.items():
        for time, value in points:
            if time in timestamps:
                idx = timestamps.index(time)
                ax.scatter(timestamps[idx], values[idx], color='orange', label=alarm, zorder=5)
                ax.annotate(alarm, (timestamps[idx], values[idx]), textcoords="offset points", xytext=(0, 10),
                            ha='center', color='orange')

    # 연결 끊어짐 시각적 표시
    for time in disconnects:
        if time in timestamps:
            idx = timestamps.index(time)
            ax.scatter(timestamps[idx], values[idx], color='black', label='Disconnect', zorder=5)
            ax.annotate('Disconnect', (timestamps[idx], values[idx]), textcoords="offset points", xytext=(0, 10),
                        ha='center', color='black')

    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys())
