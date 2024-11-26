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

# 변환 함수: 전압을 공기압으로 변환 (센서 데이터시트에 맞게 조정 필요)
def convert_to_pressure(voltage):
    # 예시 변환 공식, 실제 센서에 맞게 수정하세요
    return voltage * 10  # 임시 수식

# Tkinter GUI 클래스 정의
class PressureMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("공기압 모니터링 시스템")
        self.root.geometry("800x600")
        self.root.resizable(False, False)

        # 스타일 설정
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", font=("Helvetica", 16))
        style.configure("TButton", font=("Helvetica", 14))

        # 공기압 표시 레이블
        self.pressure_label = ttk.Label(root, text="공기압: -- Pa", foreground="blue")
        self.pressure_label.pack(pady=20)

        # Matplotlib 그래프 설정
        self.fig, self.ax = plt.subplots(figsize=(7, 4))
        self.ax.set_title("공기압 실시간 그래프")
        self.ax.set_xlabel("시간 (초)")
        self.ax.set_ylabel("압력 (Pa)")
        self.ax.set_ylim(0, 100)  # 예상 압력 범위에 맞게 조정

        # 데이터 리스트 초기화
        self.data_x = []
        self.data_y = []
        self.start_time = time.time()

        # 그래프의 선 객체 초기화
        self.line, = self.ax.plot([], [], color='red')

        # 그래프를 Tkinter에 삽입
        self.canvas = FigureCanvasTkAgg(self.fig, master=root)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack()

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
                pressure = convert_to_pressure(voltage)  # 전압을 공기압으로 변환

                # GUI 스레드에서 레이블 업데이트
                self.root.after(0, self.update_label, pressure)

                # 그래프 데이터 업데이트
                current_time = time.time() - self.start_time
                self.data_x.append(current_time)
                self.data_y.append(pressure)

                # 데이터 포인트 수 제한 (예: 100개)
                if len(self.data_x) > 100:
                    self.data_x = self.data_x[-100:]
                    self.data_y = self.data_y[-100:]

                # GUI 스레드에서 그래프 업데이트
                self.root.after(0, self.update_graph)

                time.sleep(1)  # 1초 간격으로 업데이트
            except Exception as e:
                print(f"오류 발생: {e}")
                self.running = False

    def update_label(self, pressure):
        self.pressure_label.config(text=f"공기압: {pressure:.2f} Pa")

    def update_graph(self):
        # 그래프의 데이터 업데이트
        self.line.set_data(self.data_x, self.data_y)
        self.ax.set_xlim(max(0, self.data_x[-1] - 100), self.data_x[-1] + 1)

        # y축 범위 동적 조정
        if self.data_y:
            current_max = max(self.data_y)
            if current_max + 10 > self.ax.get_ylim()[1]:
                self.ax.set_ylim(0, current_max + 10)

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
