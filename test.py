import time
import board
import busio
import adafruit_ads1x15.ads1015 as ADS  # 모듈을 ADS로 임포트
from adafruit_ads1x15.analog_in import AnalogIn
import tkinter as tk
from tkinter import ttk
from collections import deque

# 이동 평균 클래스 정의
class MovingAverage:
    def __init__(self, size=20):
        self.size = size
        self.buffer = deque(maxlen=size)
        self.total = 0.0

    def add_sample(self, sample):
        if len(self.buffer) == self.size:
            self.total -= self.buffer[0]
        self.buffer.append(sample)
        self.total += sample

    def get_average(self):
        if not self.buffer:
            return 0.0
        return self.total / len(self.buffer)

# I2C 설정
i2c = busio.I2C(board.SCL, board.SDA)

# ADS1015 객체 생성 및 PGA 설정 (gain=1 => ±4.096V)
ads = ADS.ADS1015(i2c, gain=1)

# 채널 설정 (AIN0에 연결)
chan = AnalogIn(ads, ADS.P0)  # ADS.P0 사용

# 변환 공식 (0V ~ 5V => 0 kPa ~ 2.5 kPa)
def convert_to_pressure(voltage):
    return voltage * 500  # V당 500 Pa

# 전압을 퍼센트로 변환하는 함수
def voltage_to_percentage(voltage, min_v=0.4, max_v=1.0):
    """
    주어진 전압을 0~100%로 매핑합니다.
    min_v: 0%에 해당하는 전압
    max_v: 100%에 해당하는 전압
    """
    if voltage <= min_v:
        return 0.0
    elif voltage >= max_v:
        return 100.0
    else:
        return (voltage - min_v) / (max_v - min_v) * 100.0

# 이동 평균 객체 생성
moving_average = MovingAverage(size=20)  # 샘플 수를 늘려 평균의 정확도 향상

# 여러 번 측정하여 평균을 구하는 함수
def get_average_voltage(channel, delay=0.01):
    voltage = channel.voltage
    moving_average.add_sample(voltage)
    average = moving_average.get_average()
    return average

# GUI 클래스 정의
class PressureMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Pressure Monitor")
        self.root.geometry("400x200")
        
        # 스타일 설정
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("green.Horizontal.TProgressbar", foreground='green', background='green')

        # 프로그레스 바
        self.progress = ttk.Progressbar(root, style="green.Horizontal.TProgressbar", orient="horizontal",
                                        length=300, mode="determinate", maximum=100)
        self.progress.pack(pady=20)

        # 퍼센트 레이블
        self.percent_label = ttk.Label(root, text="0.00 %", font=("Helvetica", 16))
        self.percent_label.pack()

        # 전압 레이블
        self.voltage_label = ttk.Label(root, text="Voltage: 0.00 V", font=("Helvetica", 12))
        self.voltage_label.pack()

        # 압력 레이블
        self.pressure_label = ttk.Label(root, text="Pressure: 0.00 Pa", font=("Helvetica", 12))
        self.pressure_label.pack()

        # 업데이트 주기 설정 (ms 단위)
        self.update_interval = 200  # 0.2초

        # 애니메이션 설정
        self.animation_step = 1  # 프로그레스 바 업데이트 스텝
        self.animation_delay = 10  # 각 스텝 사이의 지연 (ms)
        self.current_percentage = 0.0  # 현재 표시되는 퍼센트
        self.target_percentage = 0.0   # 목표 퍼센트

        # 업데이트 시작
        self.update_readings()

    def update_readings(self):
        try:
            average_voltage = get_average_voltage(chan, delay=0.01)
            pressure = convert_to_pressure(average_voltage)
            self.target_percentage = voltage_to_percentage(average_voltage, min_v=0.4, max_v=1.0)

            # UI 업데이트 (애니메이션을 통해 부드럽게)
            self.animate_progress()

            # 레이블 업데이트 (current_percentage 기준)
            self.voltage_label.config(text=f"Voltage: {average_voltage:.2f} V")
            self.pressure_label.config(text=f"Pressure: {pressure:.2f} Pa")
            self.percent_label.config(text=f"{self.current_percentage:.2f} %")
        except Exception as e:
            # GUI에서 오류를 표시
            self.percent_label.config(text="오류 발생")
            self.voltage_label.config(text=f"Voltage: N/A")
            self.pressure_label.config(text=f"Pressure: N/A")
            print(f"오류 발생: {e}")
        
        # 다음 업데이트 예약
        self.root.after(self.update_interval, self.update_readings)

    def animate_progress(self):
        if self.current_percentage < self.target_percentage:
            self.current_percentage += self.animation_step
            if self.current_percentage > self.target_percentage:
                self.current_percentage = self.target_percentage
        elif self.current_percentage > self.target_percentage:
            self.current_percentage -= self.animation_step
            if self.current_percentage < self.target_percentage:
                self.current_percentage = self.target_percentage
        
        # 프로그레스 바와 레이블 업데이트
        self.progress['value'] = self.current_percentage
        self.percent_label.config(text=f"{self.current_percentage:.2f} %")
        
        # 애니메이션 계속
        if self.current_percentage != self.target_percentage:
            self.root.after(self.animation_delay, self.animate_progress)

# 메인 함수
def main():
    root = tk.Tk()
    app = PressureMonitorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
