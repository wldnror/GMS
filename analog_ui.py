import os
import threading
from collections import deque
from tkinter import Frame, Canvas, StringVar
import smbus2
import queue
import asyncio
import tkinter as tk

from common import SEGMENTS, create_segment_display, SCALE

# 전역 변수 설정
GAIN = 2 / 3
SCALE_FACTOR = 1.65  # 20% 키우기
ADS1115_ADDRESSES = [0x48, 0x4A, 0x4B]  # 3개의 ADS1115 모듈 주소
ADS1115_CONVERSION_REG = 0x00
ADS1115_CONFIG_REG = 0x01

# 각 채널에 대한 MUX 설정 값
CONFIG_MUX = {
    'AIN0': 0x4000,  # AIN0 입력
    'AIN1': 0x5000,  # AIN1 입력
    'AIN2': 0x6000,  # AIN2 입력
    'AIN3': 0x7000   # AIN3 입력
}

CONFIG_GAIN_2_3 = 0x0000  # ±6.144V 범위
CONFIG_MODE_SINGLE = 0x0100
CONFIG_DR_1600SPS = 0x0080  # 1600 샘플링 속도
CONFIG_COMP_MODE_TRAD = 0x0000
CONFIG_COMP_POL_LOW = 0x0000
CONFIG_COMP_LAT_NON = 0x0000
CONFIG_COMP_QUE_DISABLE = 0x0003

class ADS1115:
    def __init__(self, address):
        self.address = address
        self.bus = smbus2.SMBus(1)  # I2C 버스 초기화

    def write_config(self, channel):
        config = (
            0x8000 |  # 시작 비트
            CONFIG_MUX[channel] |
            CONFIG_GAIN_2_3 |
            CONFIG_MODE_SINGLE |
            CONFIG_DR_1600SPS |
            CONFIG_COMP_MODE_TRAD |
            CONFIG_COMP_POL_LOW |
            CONFIG_COMP_LAT_NON |
            CONFIG_COMP_QUE_DISABLE
        )
        config_high = (config >> 8) & 0xFF
        config_low = config & 0xFF
        self.bus.write_i2c_block_data(self.address, ADS1115_CONFIG_REG, [config_high, config_low])

    def read_conversion(self):
        data = self.bus.read_i2c_block_data(self.address, ADS1115_CONVERSION_REG, 2)
        raw_value = (data[0] << 8) | data[1]
        if raw_value > 0x7FFF:
            raw_value -= 0x10000
        return raw_value

    def read_adc(self, channel):
        self.write_config(channel)
        # 비동기 컨텍스트 내에서 호출되므로 asyncio.sleep을 사용
        return asyncio.create_task(self.async_read_adc(channel))

    async def async_read_adc(self, channel):
        await asyncio.sleep(0.05)  # 변환 대기 (50ms)
        raw_value = self.read_conversion()
        voltage = raw_value * 6.144 / 32767  # ±6.144V 범위로 변환
        current = voltage / 250  # 250옴 저항 사용 시 전류 계산
        milliamp = current * 1000  # mA 단위로 변환
        return milliamp

class AnalogUI:
    GAS_FULL_SCALE = {
        "ORG": 9999,
        "ARF-T": 5000,
        "HMDS": 3000.0,
        "HC-100": 5000
    }

    GAS_TYPE_POSITIONS = {
        "ORG": (int(115 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "ARF-T": (int(107 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "HMDS": (int(110 * SCALE_FACTOR), int(95 * SCALE_FACTOR)),
        "HC-100": (int(104 * SCALE_FACTOR), int(95 * SCALE_FACTOR))
    }

    ALARM_LEVELS = {
        "ORG": {"AL1": 9500, "AL2": 9999},
        "ARF-T": {"AL1": 2000, "AL2": 4000},
        "HMDS": {"AL1": 2640.0, "AL2": 3000.0},
        "HC-100": {"AL1": 1500, "AL2": 3000}
    }

    def __init__(self, parent, num_boxes, gas_types, alarm_callback):
        self.parent = parent
        self.alarm_callback = alarm_callback
        self.num_boxes = num_boxes
        self.gas_types = {}
        self.box_states = []
        self.box_frames = []
        self.box_data = []
        self.adc_values = [deque(maxlen=3) for _ in range(num_boxes)]  # 필터링을 위해 최근 3개의 값을 유지
        self.adc_modules = [ADS1115(address) for address in ADS1115_ADDRESSES]  # 3개의 ADS1115 모듈 초기화
        self.adc_queue = queue.Queue()

        # UI 생성
        for i in range(num_boxes):
            self.create_analog_box(i, gas_types)

        for i in range(num_boxes):
            self.update_circle_state([False, False, False, False], box_index=i)

        # ADC 스레드 시작
        self.start_adc_thread()
        self.schedule_ui_update()

    def create_analog_box(self, index, initial_gas_types):
        box_frame = Frame(self.parent, highlightthickness=int(7 * SCALE_FACTOR))

        inner_frame = Frame(box_frame)
        inner_frame.pack(padx=int(1), pady=int(1))

        box_canvas = Canvas(inner_frame, width=int(150 * SCALE_FACTOR), height=int(300 * SCALE_FACTOR),
                            highlightthickness=int(3 * SCALE_FACTOR),
                            highlightbackground="#000000", highlightcolor="#000000", bg='white')
        box_canvas.pack()

        box_canvas.create_rectangle(0, 0, int(160 * SCALE_FACTOR), int(200 * SCALE_FACTOR),
                                    fill='grey', outline='grey', tags='border')
        box_canvas.create_rectangle(0, int(200 * SCALE_FACTOR), int(160 * SCALE_FACTOR), int(310 * SCALE_FACTOR),
                                    fill='black', outline='grey', tags='border')

        gas_type_value = initial_gas_types.get(f"analog_box_{index}", "ORG")
        gas_type_var = StringVar(value=gas_type_value)
        gas_type_var.trace_add("write", lambda *args, var=gas_type_var, idx=index: self.update_full_scale(var, idx))
        self.gas_types[f"analog_box_{index}"] = gas_type_var

        gas_type_text_id = box_canvas.create_text(*self.GAS_TYPE_POSITIONS[gas_type_var.get()],
                                                  text=gas_type_var.get(),
                                                  font=("Helvetica", int(16 * SCALE_FACTOR), "bold"),
                                                  fill="#cccccc", anchor="center")
        self.box_states.append({
            "previous_value": 0,
            "current_value": 0,
            "interpolating": False,
            "blink_state": False,
            "blinking_error": False,
            "previous_segment_display": None,
            "last_history_time": None,
            "last_history_value": None,
            "gas_type_text_id": gas_type_text_id,
            "full_scale": self.GAS_FULL_SCALE[gas_type_var.get()],
            "pwr_blink_state": False,
            "blink_thread": None,
            "stop_blinking": threading.Event(),
            "blink_lock": threading.Lock(),
            "alarm1_on": False,
            "alarm2_on": False,
            "milliamp_var": None,
            "milliamp_text_id": None
        })

        create_segment_display(box_canvas)
        self.update_segment_display("    ", box_canvas, box_index=index)

        circle_items = []

        circle_items.append(box_canvas.create_oval(int(77 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
                                                   int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                                                   int(87 * SCALE_FACTOR) - int(20 * SCALE_FACTOR),
                                                   int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(140 * SCALE_FACTOR) - int(35 * SCALE_FACTOR),
                               int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                               text="AL2", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(int(133 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
                                                   int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                                                   int(123 * SCALE_FACTOR) - int(30 * SCALE_FACTOR),
                                                   int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(95 * SCALE_FACTOR) - int(25 * SCALE_FACTOR),
                               int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                               text="AL1", fill="#cccccc", anchor="e")

        circle_items.append(box_canvas.create_oval(int(30 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
                                                   int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                                                   int(40 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
                                                   int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(35 * SCALE_FACTOR) - int(10 * SCALE_FACTOR),
                               int(222 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                               text="PWR", fill="#cccccc", anchor="center")

        circle_items.append(box_canvas.create_oval(int(171 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                                                   int(200 * SCALE_FACTOR) - int(32 * SCALE_FACTOR),
                                                   int(181 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                                                   int(190 * SCALE_FACTOR) - int(32 * SCALE_FACTOR)))
        box_canvas.create_text(int(175 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                               int(217 * SCALE_FACTOR) - int(40 * SCALE_FACTOR),
                               text="FUT", fill="#cccccc", anchor="n")

        box_canvas.create_text(int(80 * SCALE_FACTOR), int(270 * SCALE_FACTOR), text="GMS-1000",
                               font=("Helvetica", int(16 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")

        milliamp_var = StringVar(value="4-20 mA")
        milliamp_text_id = box_canvas.create_text(int(80 * SCALE_FACTOR), int(240 * SCALE_FACTOR),
                                                  text=milliamp_var.get(),
                                                  font=("Helvetica", int(10 * SCALE_FACTOR), "bold"),
                                                  fill="#00ff00", anchor="center")
        self.box_states[index]["milliamp_var"] = milliamp_var
        self.box_states[index]["milliamp_text_id"] = milliamp_text_id

        box_canvas.create_text(int(80 * SCALE_FACTOR), int(295 * SCALE_FACTOR), text="GDS ENGINEERING CO.,LTD",
                               font=("Helvetica", int(7 * SCALE_FACTOR), "bold"), fill="#cccccc", anchor="center")

        self.box_frames.append(box_frame)
        self.box_data.append((box_canvas, circle_items))

    def update_full_scale(self, gas_type_var, box_index):
        gas_type = gas_type_var.get()
        full_scale = self.GAS_FULL_SCALE[gas_type]
        self.box_states[box_index]["full_scale"] = full_scale

        box_canvas = self.box_data[box_index][0]
        position = self.GAS_TYPE_POSITIONS[gas_type]
        box_canvas.coords(self.box_states[box_index]["gas_type_text_id"], *position)
        box_canvas.itemconfig(self.box_states[box_index]["gas_type_text_id"], text=gas_type)

    def update_circle_state(self, states, box_index=0):
        box_canvas, circle_items = self.box_data[box_index]

        colors_on = ['red', 'red', 'green', 'yellow']
        colors_off = ['#fdc8c8', '#fdc8c8', '#e0fbba', '#fcf1bf']
        outline_colors = ['#ff0000', '#ff0000', '#00ff00', '#ffff00']
        outline_color_off = '#000000'

        if states[1]:
            states[0] = True

        for i, state in enumerate(states[:4]):
            color = colors_on[i] if state else colors_off[i]
            box_canvas.itemconfig(circle_items[i], fill=color, outline=color)

        alarm_active = states[0] or states[1]
        self.alarm_callback(alarm_active, f"analog_{box_index}")

        if states[1]:
            outline_color = outline_colors[1]
        elif states[0]:
            outline_color = outline_colors[0]
        elif states[3]:
            outline_color = outline_colors[3]
        else:
            outline_color = outline_color_off

        box_canvas.config(highlightbackground=outline_color)

    def update_segment_display(self, value, box_canvas, blink=False, box_index=0):
        previous_segment_display = self.box_states[box_index]["previous_segment_display"]

        if value != previous_segment_display:
            self.box_states[box_index]["previous_segment_display"] = value
            self.schedule_segment_update(box_canvas, value, box_index, blink)

    def schedule_segment_update(self, box_canvas, value, box_index, blink):
        segment_queue = self.box_states[box_index].get("segment_queue", None)
        if not segment_queue:
            segment_queue = queue.Queue(maxsize=10)
            self.box_states[box_index]["segment_queue"] = segment_queue

        if not segment_queue.full():
            segment_queue.put((value, blink))

        if not self.box_states[box_index].get("segment_updating", False):
            self.box_states[box_index]["segment_updating"] = True
            self.parent.after(10, self.process_segment_queue, box_canvas, box_index)

    def process_segment_queue(self, box_canvas, box_index):
        segment_queue = self.box_states[box_index]["segment_queue"]
        if not segment_queue.empty():
            value, blink = segment_queue.get()
            self.perform_segment_update(box_canvas, value, blink, box_index)
            self.parent.after(10, self.process_segment_queue, box_canvas, box_index)
        else:
            self.box_states[box_index]["segment_updating"] = False

    def perform_segment_update(self, box_canvas, value, blink, box_index):
        value = value.strip()
        gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
        gas_type = gas_type_var.get()

        digits = []
        decimal_positions = [False, False, False, False]

        if '.' in value:
            dot_index = value.find('.')
            digits = list(value.replace('.', ''))
            digits = [' '] * (4 - len(digits)) + digits  # 왼쪽에 공백 추가하여 길이 4로 맞춤
            adjusted_dot_index = dot_index - (len(value) - 4)  # 수정된 부분
            if 0 <= adjusted_dot_index < 4:
                decimal_positions[adjusted_dot_index] = True
        else:
            digits = list(value.rjust(4))

        leading_zero = True

        for index in range(4):
            if index >= len(digits):
                break

            digit = digits[index]

            if leading_zero and digit == '0' and index < 3:
                segments = SEGMENTS[' ']
            else:
                segments = SEGMENTS.get(digit, SEGMENTS[' '])
                leading_zero = False

            if blink and self.box_states[box_index]["blink_state"]:
                segments = SEGMENTS[' ']

            for j, state in enumerate(segments):
                color = '#fc0c0c' if state == '1' else '#424242'
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_{chr(97 + j)}', fill=color)

            if decimal_positions[index]:
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_dot', fill='#fc0c0c')
            else:
                box_canvas.segment_canvas.itemconfig(f'segment_{index}_dot', fill='#424242')

        self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

    def async_write_log(self, log_file, log_line):
        with open(log_file, 'a') as file:
            file.write(log_line)

    async def read_adc_data(self):
        while True:
            tasks = []
            for adc_index, adc in enumerate(self.adc_modules):
                for channel in ['AIN0', 'AIN1', 'AIN2', 'AIN3']:
                    task = self.read_adc_values(adc, adc_index, channel)
                    tasks.append(task)
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)

    async def read_adc_values(self, adc, adc_index, channel):
        try:
            milliamp = await adc.read_adc(channel)
            box_index = adc_index * 4 + list(CONFIG_MUX.keys()).index(channel)

            # 필터링 적용
            if len(self.adc_values[box_index]) == 0:
                filtered_value = milliamp
            else:
                filtered_value = (0.7 * milliamp) + (0.3 * self.adc_values[box_index][-1])

            self.adc_values[box_index].append(filtered_value)
            print(f"ADC {adc_index} Channel {channel} Current: {filtered_value:.2f} mA")
            self.adc_queue.put(box_index)
        except Exception as e:
            print(f"Error reading ADC data from ADC {adc_index} Channel {channel}: {e}")

    def start_adc_thread(self):
        adc_thread = threading.Thread(target=self.run_async_adc)
        adc_thread.daemon = True
        adc_thread.start()

    def run_async_adc(self):
        asyncio.run(self.read_adc_data())

    def schedule_ui_update(self):
        self.parent.after(100, self.update_ui_from_queue)

    def update_ui_from_queue(self):
        try:
            while not self.adc_queue.empty():
                box_index = self.adc_queue.get_nowait()
                gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
                gas_type = gas_type_var.get()
                full_scale = self.GAS_FULL_SCALE[gas_type]
                alarm_levels = self.ALARM_LEVELS[gas_type]

                self.start_interpolation(box_index, full_scale, alarm_levels)

        except Exception as e:
            print(f"Error updating UI from queue: {e}")

        self.schedule_ui_update()

    def start_interpolation(self, box_index, full_scale, alarm_levels):
        if self.box_states[box_index]["interpolating"]:
            prev_value = self.box_states[box_index]["previous_value"]
            curr_value = self.box_states[box_index]["current_value"]

            change = abs(curr_value - prev_value)
            SOME_THRESHOLD = 5.0  # 예시로 5mA 이상의 변화 시

            if change > SOME_THRESHOLD:
                # 큰 변화 시 인터폴레이션 생략하고 즉시 업데이트
                self.box_states[box_index]["interpolating"] = False
                self.update_display_immediately(box_index, curr_value, full_scale, alarm_levels)
            else:
                steps = 10
                interval = 10
                self.animate_step(box_index, 0, steps, prev_value, curr_value, full_scale, alarm_levels, interval)

    def animate_step(self, box_index, step, total_steps, prev_value, curr_value, full_scale, alarm_levels, interval):
        if step >= total_steps:
            self.box_states[box_index]["interpolating"] = False
            return

        interpolated_value = prev_value + (curr_value - prev_value) * (step / total_steps)
        formatted_value = ((interpolated_value - 4) / (20 - 4)) * full_scale
        formatted_value = max(0.0, min(formatted_value, full_scale))

        pwr_on = interpolated_value >= 1.5

        gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
        gas_type = gas_type_var.get()

        if gas_type == "HMDS":
            display_value = f"{formatted_value / 10:4.1f}"
        else:
            display_value = f"{int(formatted_value):>4}"

        self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
        self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

        self.update_circle_state([self.box_states[box_index]["alarm1_on"],
                                  self.box_states[box_index]["alarm2_on"], pwr_on, False], box_index=box_index)

        if pwr_on:
            self.update_segment_display(display_value, self.box_data[box_index][0],
                                        blink=False, box_index=box_index)
        else:
            self.update_segment_display("    ", self.box_data[box_index][0],
                                        blink=False, box_index=box_index)

        milliamp_text = f"{interpolated_value:.1f} mA" if pwr_on else "PWR OFF"
        milliamp_color = "#00ff00" if pwr_on else "#ff0000"
        self.box_states[box_index]["milliamp_var"].set(milliamp_text)
        box_canvas = self.box_data[box_index][0]
        box_canvas.itemconfig(self.box_states[box_index]["milliamp_text_id"],
                              text=milliamp_text, fill=milliamp_color)

        self.parent.after(interval, self.animate_step, box_index, step + 1, total_steps,
                          prev_value, curr_value, full_scale, alarm_levels, interval)

    def update_display_immediately(self, box_index, current_value, full_scale, alarm_levels):
        formatted_value = ((current_value - 4) / (20 - 4)) * full_scale
        formatted_value = max(0.0, min(formatted_value, full_scale))

        pwr_on = current_value >= 1.5

        gas_type_var = self.gas_types.get(f"analog_box_{box_index}", StringVar(value="ORG"))
        gas_type = gas_type_var.get()

        if gas_type == "HMDS":
            display_value = f"{formatted_value / 10:4.1f}"
        else:
            display_value = f"{int(formatted_value):>4}"

        self.box_states[box_index]["alarm1_on"] = formatted_value >= alarm_levels["AL1"]
        self.box_states[box_index]["alarm2_on"] = formatted_value >= alarm_levels["AL2"] if pwr_on else False

        self.update_circle_state([self.box_states[box_index]["alarm1_on"],
                                  self.box_states[box_index]["alarm2_on"], pwr_on, False], box_index=box_index)

        if pwr_on:
            self.update_segment_display(display_value, self.box_data[box_index][0],
                                        blink=False, box_index=box_index)
        else:
            self.update_segment_display("    ", self.box_data[box_index][0],
                                        blink=False, box_index=box_index)

        milliamp_text = f"{current_value:.1f} mA" if pwr_on else "PWR OFF"
        milliamp_color = "#00ff00" if pwr_on else "#ff0000"
        self.box_states[box_index]["milliamp_var"].set(milliamp_text)
        box_canvas = self.box_data[box_index][0]
        box_canvas.itemconfig(self.box_states[box_index]["milliamp_text_id"],
                              text=milliamp_text, fill=milliamp_color)

    def blink_alarm(self, box_index, is_second_alarm):
        def toggle_color():
            with self.box_states[box_index]["blink_lock"]:
                if is_second_alarm:
                    self.update_circle_state([True, self.box_states[box_index]["blink_state"],
                                              True, False], box_index=box_index)
                else:
                    self.update_circle_state([self.box_states[box_index]["blink_state"],
                                              False, True, False], box_index=box_index)

                self.box_states[box_index]["blink_state"] = not self.box_states[box_index]["blink_state"]

                if self.box_states[box_index]["current_value"] is not None:
                    self.update_segment_display(str(self.box_states[box_index]["current_value"]),
                                                self.box_data[box_index][0], blink=False, box_index=box_index)

                if not self.box_states[box_index]["stop_blinking"].is_set():
                    self.parent.after(1000, toggle_color) if is_second_alarm else self.parent.after(600, toggle_color)

        toggle_color()

# 애플리케이션 실행
if __name__ == "__main__":
    def alarm_callback(active, identifier):
        if active:
            print(f"Alarm active on {identifier}")
        else:
            print(f"Alarm cleared on {identifier}")

    root = tk.Tk()
    root.title("Analog UI")
    num_boxes = 12  # 3개 모듈 × 4채널
    gas_types = {f"analog_box_{i}": "ORG" for i in range(num_boxes)}  # 초기 가스 타입 설정
    analog_ui = AnalogUI(root, num_boxes, gas_types, alarm_callback)
    root.mainloop()
