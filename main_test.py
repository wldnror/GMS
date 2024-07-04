import tkinter as tk
from threading import Thread
import numpy as np
from smbus2 import SMBus
import time
import joblib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# I2C 버스 번호 및 주소
BUS_NUMBER = 1
DEVICE_ADDRESS = 0x54

# 전역 변수로 bus 객체 선언 및 초기화
bus = SMBus(BUS_NUMBER)

time_steps = 60  # 전역 변수로 선언
current_values = []  # 전역 변수로 초기화
clf = joblib.load('gas_classifier.pkl')

# I2C 버스 재설정 함수
def reset_i2c_bus():
    global bus
    try:
        bus.close()
        time.sleep(0.5)
        bus = SMBus(BUS_NUMBER)
        time.sleep(0.5)
        print("I2C 버스 재설정 완료")
    except Exception as e:
        print(f"I2C 버스 재설정 오류: {e}")

# 센서 데이터 읽기 함수
def read_sensor_data(retries=5):
    for attempt in range(retries):
        try:
            bus.write_byte(DEVICE_ADDRESS, 0x52)
            time.sleep(0.1)
            data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 7)
            if data[0] == 0x08:
                concentration = (data[1] << 8) | data[2]
                if 0 <= concentration <= 20000:
                    return concentration
                else:
                    print(f"비정상적인 농도 값: {concentration}")
            else:
                print("잘못된 헤더 바이트")
        except Exception as e:
            print(f"센서 읽기 오류: {e}")
            if attempt == retries - 1:
                reset_i2c_bus()
        time.sleep(0.5)  # 재시도 전에 약간의 지연을 줍니다
    return None

# 임계값을 사용한 예측 함수
def predict_with_threshold(model, data, threshold=0.3):
    proba = model.predict_proba(data)
    return (proba[:, 1] > threshold).astype(int)

# 실시간 센서 데이터 출력 및 예측 함수
def print_and_predict_sensor_data():
    global current_values
    while True:
        data = read_sensor_data()
        if data is not None:
            print(f"실시간 가스 농도: {data} ppm")
            if data == 0:
                current_values = []
                result.set("대기 중")
            else:
                if len(current_values) >= time_steps:
                    current_values.pop(0)
                current_values.append(data)
                # 실시간 예측
                if len(current_values) >= 23:
                    features = np.array(current_values[-23:]).reshape(1, -1)
                    prediction = predict_with_threshold(clf, features, threshold=0.6)  # 예측 임계값을 0.6으로 조정
                    if prediction[0] == 0:
                        result.set("에탄올입니다")
                    elif prediction[0] == 3:
                        result.set("IPA입니다")
        time.sleep(1)

# 실시간 그래프 업데이트 함수
def update_graph(frame):
    if len(current_values) > 0:
        line.set_ydata(current_values[-time_steps:] if len(current_values) >= time_steps else current_values)
        line.set_xdata(range(len(current_values[-time_steps:])) if len(current_values) >= time_steps else range(len(current_values)))
        ax.relim()
        ax.autoscale_view()
    return line,

# GUI 생성
root = tk.Tk()
root.title("가스 감지기")

# 실시간 예측 결과 라벨
result = tk.StringVar()
result.set("대기 중")
tk.Label(root, textvariable=result, font=("Helvetica", 16)).grid(row=0, columnspan=2)

# 실시간 그래프 표시
fig, ax = plt.subplots()
line, = ax.plot([], [], lw=2)
canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().grid(row=1, columnspan=2)
ani = FuncAnimation(fig, update_graph, interval=1000, cache_frame_data=False)

# 실시간 센서 데이터 출력 및 예측 스레드 시작
sensor_thread = Thread(target=print_and_predict_sensor_data)
sensor_thread.daemon = True
sensor_thread.start()

# GUI 루프 시작
root.mainloop()
