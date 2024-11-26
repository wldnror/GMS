import time
import board
import busio
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import tkinter as tk
from tkinter import ttk
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import sys

# Matplotlib 백엔드 설정
matplotlib.use("TkAgg")

# I2C 설정
i2c = busio.I2C(board.SCL, board.SDA)

# ADS1015 객체 생성
ads = ADS.ADS1015(i2c)

# 채널 설정 (AIN0에 연결)
chan = AnalogIn(ads, ADS.P0)

# 변환 함수: 전압을 공기압 퍼센트로 변환
def convert_to_pressure_percent(voltage):
    # 센서의 출력 범위: 0V -> 0%, 5V -> 100%
    # 압력 퍼센트 = 전압 * 20
    pressure_percent = voltage * 20
    # 범위 제한
    if pressure_percent < 0:
        pressure_percent = 0
    elif pressure_percent > 100:
        pressure_percent = 100
    return pressure_percent

# Tkinter GUI 클래스 정의
class PressureMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("공기압 모니터링 시스템")
        self.root.geometry("400x300")
        self.root.resizable(False, False)

        # 스타일 설정
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", font=("Helvetica", 16))
        style.configure("TButton", font=("Helvetica", 14))

        # 공기압 표시 레이블
        self.pressure_label = ttk.Label(root, text="공기압: -- %", foreground="blue")
        self.pressure_label.pack(pady=20)

        # Matplotlib 그래프 설정
        self.fig, self.ax = plt.subplots(figsize=(4, 2))
        self.ax.set_title("실시간 공기압")
        self.ax.set_ylim(0, 100)  # 0~100% 범위 설정
        self.ax.set_xlim(0, 1)    # 단일 막대 표시
        self.ax.axis('off')       # 축 숨기기

        # 초기 막대 생성
        self.bar = self.ax.barh(0, 0, color='green', height=0.5)

        # 그래프를 Tkinter에 삽입
        self.canvas = FigureCanvasTkAgg(self.fig, master=root)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(pady=10)

        # 데이터 업데이트 스레드 시작
        self.running = True
        self.update_thread = threading.Thread(target=self.update_data)
        self.update_thread.start()

        # 창 닫기 이벤트 처리
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def update_data(self):
        while self.running:
            try:
                voltage = chan.voltage  # 센서로부터 전압 읽기
                pressure = convert_to_pressure_percent(voltage)  # 전압을 공기압 퍼센트로 변환

                # GUI 스레드에서 레이블 업데이트
                self.root.after(0, self.update_label, pressure)

                # GUI 스레드에서 그래프 업데이트
                self.root.after(0, self.update_graph, pressure)

                time.sleep(0.2)  # 200ms 간격으로 업데이트
            except Exception as e:
                print(f"오류 발생: {e}")
                self.running = False

    def update_label(self, pressure):
        self.pressure_label.config(text=f"공기압: {pressure:.2f} %")

    def update_graph(self, pressure):
        # 막대 그래프 업데이트
        self.bar[0].set_width(pressure)
        # 색상 변경 (선택 사항): 낮은 압력은 파랑, 높은 압력은 빨강
        if pressure < 50:
            self.bar[0].set_color('blue')
        elif pressure < 80:
            self.bar[0].set_color('yellow')
        else:
            self.bar[0].set_color('red')

        # 그래프 리프레시
        self.ax.figure.canvas.draw()

    def on_closing(self):
        self.running = False
        self.update_thread.join()
        self.root.destroy()
        sys.exit()

# 메인 실행
if __name__ == "__main__":
    root = tk.Tk()
    app = PressureMonitorApp(root)
    root.mainloop()
