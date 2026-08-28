"""Shared drawing constants and helpers for GMS Tkinter panels."""

from __future__ import annotations

from tkinter import Canvas

from PIL import Image

from ui_config import SEGMENT_SCALE

SEGMENT_ON = "#fc0c0c"
SEGMENT_OFF = "#424242"

SEGMENTS = {
    "0": "11111100",
    "1": "01100000",
    "2": "11011010",
    "3": "11110010",
    "4": "01100110",
    "5": "10110110",
    "6": "10111110",
    "7": "11100000",
    "8": "11111110",
    "9": "11110110",
    "E": "10011110",
    "n": "00101010",
    "d": "01111010",
    "r": "00001010",
    "-": "00000010",
    " ": "00000000",
    ".": "00000001",
}

BIT_TO_SEGMENT = {
    0: "E-10",
    1: "E-22",
    2: "E-12",
    3: "E-23",
    4: "DOT",
}

SCALE = SEGMENT_SCALE
X_SHIFT = 0
Y_SHIFT = 0


def create_gradient_bar(width: int, height: int) -> Image.Image:
    width = max(1, int(width))
    height = max(1, int(height))
    gradient = Image.new("RGB", (width, height), color=0)
    denominator = max(1, width - 1)
    for x in range(width):
        ratio = x / denominator
        if ratio < 0.25:
            red = int(255 * ratio * 4)
            green = 255
            blue = 0
        elif ratio < 0.5:
            red = 255
            green = int(255 - 255 * (ratio - 0.25) * 4)
            blue = 0
        elif ratio < 0.75:
            red = 255
            green = 0
            blue = int(255 * (ratio - 0.5) * 4)
        else:
            red = int(255 - 255 * (ratio - 0.75) * 4)
            green = 0
            blue = 255
        color = (max(0, min(255, red)), max(0, min(255, green)), max(0, min(255, blue)))
        for y in range(height):
            gradient.putpixel((x, y), color)
    return gradient


def create_segment_display(box_canvas: Canvas) -> None:
    scale = SCALE
    segment_canvas = Canvas(
        box_canvas,
        width=int((131 + X_SHIFT) * scale),
        height=int((60 + Y_SHIFT) * scale),
        bg="#000000",
        highlightthickness=0,
    )
    segment_canvas.place(x=int((20 + X_SHIFT) * scale), y=int((24 + Y_SHIFT) * scale))

    segment_items: list[list[int]] = []
    for digit_index in range(4):
        x_offset = (digit_index * 29 + 14) * scale
        segments = [
            segment_canvas.create_polygon(
                4 * scale + x_offset,
                11.2 * scale,
                12 * scale + x_offset,
                11.2 * scale,
                16 * scale + x_offset,
                13.6 * scale,
                12 * scale + x_offset,
                16 * scale,
                4 * scale + x_offset,
                16 * scale,
                x_offset,
                13.6 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_a",
            ),
            segment_canvas.create_polygon(
                16 * scale + x_offset,
                15 * scale,
                17.6 * scale + x_offset,
                17.4 * scale,
                17.6 * scale + x_offset,
                27.4 * scale,
                16 * scale + x_offset,
                29.4 * scale,
                14.4 * scale + x_offset,
                27.4 * scale,
                14.4 * scale + x_offset,
                17.4 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_b",
            ),
            segment_canvas.create_polygon(
                16 * scale + x_offset,
                31 * scale,
                17.6 * scale + x_offset,
                33.4 * scale,
                17.6 * scale + x_offset,
                43.4 * scale,
                16 * scale + x_offset,
                45.4 * scale,
                14.4 * scale + x_offset,
                43.4 * scale,
                14.4 * scale + x_offset,
                33.4 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_c",
            ),
            segment_canvas.create_polygon(
                4 * scale + x_offset,
                43.8 * scale,
                12 * scale + x_offset,
                43.8 * scale,
                16 * scale + x_offset,
                46.2 * scale,
                12 * scale + x_offset,
                48.6 * scale,
                4 * scale + x_offset,
                48.6 * scale,
                x_offset,
                46.2 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_d",
            ),
            segment_canvas.create_polygon(
                x_offset,
                31 * scale,
                1.6 * scale + x_offset,
                33.4 * scale,
                1.6 * scale + x_offset,
                43.4 * scale,
                x_offset,
                45.4 * scale,
                -1.6 * scale + x_offset,
                43.4 * scale,
                -1.6 * scale + x_offset,
                33.4 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_e",
            ),
            segment_canvas.create_polygon(
                x_offset,
                15 * scale,
                1.6 * scale + x_offset,
                17.4 * scale,
                1.6 * scale + x_offset,
                27.4 * scale,
                x_offset,
                29.4 * scale,
                -1.6 * scale + x_offset,
                27.4 * scale,
                -1.6 * scale + x_offset,
                17.4 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_f",
            ),
            segment_canvas.create_polygon(
                4 * scale + x_offset,
                27.8 * scale,
                12 * scale + x_offset,
                27.8 * scale,
                16 * scale + x_offset,
                30.2 * scale,
                12 * scale + x_offset,
                32.6 * scale,
                4 * scale + x_offset,
                32.6 * scale,
                x_offset,
                30.2 * scale,
                fill=SEGMENT_OFF,
                tags=f"segment_{digit_index}_g",
            ),
        ]
        dot = segment_canvas.create_oval(
            (32 + digit_index * 29) * scale,
            45 * scale,
            (36 + digit_index * 29) * scale,
            49 * scale,
            fill=SEGMENT_OFF,
            outline=SEGMENT_OFF,
            tags=f"segment_{digit_index}_dot",
        )
        segments.append(dot)
        segment_items.append(segments)

    box_canvas.segment_canvas = segment_canvas
    box_canvas.segment_items = segment_items


def update_segments(display_canvas: Canvas, segment_values) -> None:
    for digit_index, value in enumerate(segment_values[:4]):
        pattern = SEGMENTS.get(str(value), SEGMENTS[" "])
        segments = display_canvas.segment_items[digit_index]
        for segment_index, segment in enumerate(segments):
            on = segment_index < len(pattern) and pattern[segment_index] == "1"
            color = SEGMENT_ON if on else SEGMENT_OFF
            display_canvas.segment_canvas.itemconfig(segment, fill=color, outline=color)
