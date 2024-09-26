# common.py

from tkinter import Canvas
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

# 세그먼트 표시 매핑
SEGMENTS = {
    '0': '11111100',
    '1': '01100000',
    '2': '11011010',
    '3': '11110010',
    '4': '01100110',
    '5': '10110110',
    '6': '10111110',
    '7': '11100000',
    '8': '11111110',
    '9': '11110110',
    'E': '10011110',  # a, f, e, g, d
    '-': '00000010',  # g
    ' ': '00000000',  # 모든 세그먼트 꺼짐
    '.': '00000001'   # 점만 켜짐
}

# 확대 배율
SCALE = 1.51
# 위치 이동 값
x_shift = 0
y_shift = 0

def create_segment_display(box_canvas):
    segment_canvas = Canvas(box_canvas, width=(131 + x_shift) * SCALE, height=(60 + y_shift) * SCALE, bg='#000000', highlightthickness=0)
    segment_canvas.place(x=(20 + x_shift) * SCALE, y=(24 + y_shift) * SCALE)  # 전체 위치 이동 적용

    segment_items = []
    for i in range(4):
        x_offset = (i * 29 + 14) * SCALE  # x축 위치는 각 세그먼트에 맞게 조정
        y_offset = 0  # y축 위치는 동일하게 유지
        segments = [
            # 상단
            segment_canvas.create_polygon(4 * SCALE + x_offset, 11.2 * SCALE + y_offset, 12 * SCALE + x_offset, 11.2 * SCALE + y_offset, 16 * SCALE + x_offset, 13.6 * SCALE + y_offset,
                                          12 * SCALE + x_offset, 16 * SCALE + y_offset, 4 * SCALE + x_offset, 16 * SCALE + y_offset, 0 * SCALE + x_offset, 13.6 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_a'),

            # 상단-오른쪽
            segment_canvas.create_polygon(16 * SCALE + x_offset, 15 * SCALE + y_offset, 17.6 * SCALE + x_offset, 17.4 * SCALE + y_offset, 17.6 * SCALE + x_offset, 27.4 * SCALE + y_offset,
                                          16 * SCALE + x_offset, 29.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 27.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 17.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_b'),

            # 하단-오른쪽
            segment_canvas.create_polygon(16 * SCALE + x_offset, 31 * SCALE + y_offset, 17.6 * SCALE + x_offset, 33.4 * SCALE + y_offset, 17.6 * SCALE + x_offset, 43.4 * SCALE + y_offset,
                                          16 * SCALE + x_offset, 45.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 43.4 * SCALE + y_offset, 14.4 * SCALE + x_offset, 33.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_c'),

            # 하단
            segment_canvas.create_polygon(4 * SCALE + x_offset, 43.8 * SCALE + y_offset, 12 * SCALE + x_offset, 43.8 * SCALE + y_offset, 16 * SCALE + x_offset, 46.2 * SCALE + y_offset,
                                          12 * SCALE + x_offset, 48.6 * SCALE + y_offset, 4 * SCALE + x_offset, 48.6 * SCALE + y_offset, 0 * SCALE + x_offset, 46.2 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_d'),

            # 하단-왼쪽
            segment_canvas.create_polygon(0 * SCALE + x_offset, 31 * SCALE + y_offset, 1.6 * SCALE + x_offset, 33.4 * SCALE + y_offset, 1.6 * SCALE + x_offset, 43.4 * SCALE + y_offset,
                                          0 * SCALE + x_offset, 45.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 43.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 33.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_e'),

            # 상단-왼쪽
            segment_canvas.create_polygon(0 * SCALE + x_offset, 15 * SCALE + y_offset, 1.6 * SCALE + x_offset, 17.4 * SCALE + y_offset, 1.6 * SCALE + x_offset, 27.4 * SCALE + y_offset,
                                          0 * SCALE + x_offset, 29.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 27.4 * SCALE + y_offset, -1.6 * SCALE + x_offset, 17.4 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_f'),

            # 중간
            segment_canvas.create_polygon(4 * SCALE + x_offset, 27.8 * SCALE + y_offset, 12 * SCALE + x_offset, 27.8 * SCALE + y_offset, 16 * SCALE + x_offset, 30.2 * SCALE + y_offset,
                                          12 * SCALE + x_offset, 32.6 * SCALE + y_offset, 4 * SCALE + x_offset, 32.6 * SCALE + y_offset, 0 * SCALE + x_offset, 30.2 * SCALE + y_offset, fill='#424242',
                                          tags=f'segment_{i}_g')
        ]

        # 각 세그먼트의 점 추가 (기본으로 꺼져 있음)
        dot = segment_canvas.create_oval((26 + i * 29 + 6) * SCALE, (54 - 9) * SCALE, (30 + i * 29 + 6) * SCALE, (58 - 9) * SCALE,
                                         fill='#424242', outline='#424242', tags=f'segment_{i}_dot')

        segments.append(dot)
        segment_items.append(segments)

    box_canvas.segment_canvas = segment_canvas
    box_canvas.segment_items = segment_items
