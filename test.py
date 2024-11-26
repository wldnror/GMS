import time
import board
import busio
import adafruit_ads1x15.ads1015 as ADS  # 모듈을 ADS로 임포트
from adafruit_ads1x15.analog_in import AnalogIn
import tkinter as tk
from tkinter import ttk

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
def voltage_to_percentage(voltage, min_v=0.35, max_v=5):
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

# 여러 번 측정하여 평균을 구하는 함수
def get_average_voltage(channel, samples=300, delay=0.01):
    total = 0.0
    for _ in range(samples):
        total += channel.voltage
        time.sleep(delay)  # 각 샘플 사이의 지연 (초)
    average = total / samples
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
        self.update_interval = 1000  # 1초

        # 애니메이션 관련 변수
        self.current_percentage = 0.0
        self.target_percentage = 0.0
        self.animation_steps = 20  # 애니메이션 단계를 조정하여 속도 변경 가능
        self.animation_delay = 20  # 애니메이션 각 단계의 지연 시간 (ms)

        # 업데이트 시작
        self.update_readings()

    def update_readings(self):
        try:
            voltage = get_average_voltage(chan, samples=10, delay=0.01)
            pressure = convert_to_pressure(voltage)
            percentage = voltage_to_percentage(voltage, min_v=0.4, max_v=1.0)

            # 목표 퍼센트 업데이트
            self.target_percentage = percentage

            # UI 업데이트 (애니메이션 시작)
            self.animate_progress()

            # 레이블 업데이트
            self.percent_label.config(text=f"{percentage:.2f} %")
            self.voltage_label.config(text=f"Voltage: {voltage:.2f} V")
            self.pressure_label.config(text=f"Pressure: {pressure:.2f} Pa")
        except Exception as e:
            # GUI에서 오류를 표시
            self.percent_label.config(text="오류 발생")
            self.voltage_label.config(text="Voltage: N/A")
            self.pressure_label.config(text="Pressure: N/A")
            print(f"오류 발생: {e}")
        
        # 다음 업데이트 예약
        self.root.after(self.update_interval, self.update_readings)

    def animate_progress(self):
        if self.current_percentage < self.target_percentage:
            self.current_percentage += (self.target_percentage - self.current_percentage) / self.animation_steps
            if self.current_percentage > self.target_percentage:
                self.current_percentage = self.target_percentage
        elif self.current_percentage > self.target_percentage:
            self.current_percentage -= (self.current_percentage - self.target_percentage) / self.animation_steps
            if self.current_percentage < self.target_percentage:
                self.current_percentage = self.target_percentage

        self.progress['value'] = self.current_percentage

        # 애니메이션이 완료되지 않았다면 계속 애니메이션 진행
        if abs(self.current_percentage - self.target_percentage) > 0.1:
            self.root.after(self.animation_delay, self.animate_progress)
        else:
            self.current_percentage = self.target_percentage
            self.progress['value'] = self.current_percentage

# 메인 함수
def main():
    root = tk.Tk()
    app = PressureMonitorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
