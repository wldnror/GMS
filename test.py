import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from smbus2 import SMBus
from matplotlib import font_manager as fm, rc
import tkinter as tk
from tkinter import Button
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from sklearn.linear_model import LogisticRegression
import numpy as np

# 한글 폰트 설정
font_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'  # 폰트 경로를 적절히 설정하세요
font_name = fm.FontProperties(fname=font_path).get_name()
rc('font', family=font_name)

# I2C 버스 번호
BUS_NUMBER = 1

# I2C 주소
DEVICE_ADDRESS = 0x54

# I2C 버스 초기화
bus = SMBus(BUS_NUMBER)

# 데이터 저장 리스트
ipa_data = []
ethanol_data = []
current_data = []  # 현재 측정 데이터를 저장할 리스트

# 현재 측정 중인 데이터 타입 (True for IPA, False for Ethanol)
measuring_ipa = True
measuring = False  # 측정 시작 여부
waiting_for_drop = False  # 가스 농도 감소 대기 여부
waiting_for_injection = False  # 가스 주입 대기 여부
start_time = None  # 측정 시작 시간
toast_end_time = None  # 토스트 메시지 종료 시간
toast_message = ""  # 토스트 메시지 내용

# 초기화 그래프
fig, ax = plt.subplots()
line_ipa, = ax.plot([], [], lw=2, label="IPA", color='blue')
line_ethanol, = ax.plot([], [], lw=2, label="에탄올", color='red')
ax.set_xlim(0, 60)  # x축 범위 (시간)
ax.set_ylim(0, 20000)  # y축 범위 (센서 데이터 값 범위, 예시로 0-20000 ppm 설정)
ax.set_title("IR 가스 센서 데이터")
ax.set_xlabel("시간 (초)")
ax.set_ylabel("가스 농도 (ppm)")
ax.legend()

# 시간 표시 추가
elapsed_time_text = ax.text(0.95, 0.95, '', transform=ax.transAxes, ha='right', va='top', fontsize=12)
# 토스트 메시지 표시 추가
toast_text = ax.text(0.5, 0.1, '', transform=ax.transAxes, ha='center', va='center', fontsize=12, bbox=dict(facecolor='red', alpha=0.5))

# 머신러닝 모델 학습 (간단한 예로 Logistic Regression 사용)
X_train = []  # 입력 데이터 (특징 벡터)
y_train = []  # 출력 데이터 (0: IPA, 1: 에탄올)

def extract_features(data):
    max_value = np.max(data)
    time_to_peak = np.argmax(data)
    return [max_value, time_to_peak]

# 예시 데이터로 모델 학습
# ipa_samples와 ethanol_samples는 각각 IPA와 에탄올 측정 데이터를 포함하는 리스트입니다.
ipa_samples = [ipa_data1, ipa_data2, ipa_data3]  # IPA 데이터 샘플 리스트
ethanol_samples = [ethanol_data1, ethanol_data2, ethanol_data3]  # 에탄올 데이터 샘플 리스트

for sample in ipa_samples:
    X_train.append(extract_features(sample))
    y_train.append(0)

for sample in ethanol_samples:
    X_train.append(extract_features(sample))
    y_train.append(1)

model = LogisticRegression()
model.fit(X_train, y_train)

def predict_gas_type(data):
    features = extract_features(data)
    prediction = model.predict([features])
    return "IPA" if prediction == 0 else "에탄올"

# 실시간 데이터 업데이트 함수
def init():
    line_ipa.set_data([], [])
    line_ethanol.set_data([], [])
    elapsed_time_text.set_text('')
    toast_text.set_text('')
    return line_ipa, line_ethanol, elapsed_time_text, toast_text

def read_sensor_data():
    try:
        # 명령어를 보내 데이터 읽기 준비
        bus.write_byte(DEVICE_ADDRESS, 0x52)  # ASCII 'R'
        time.sleep(0.1)  # 데이터 준비 시간
        
        # 7 바이트 데이터 읽기
        data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 7)
        print(f"Raw data: {data}")

        if data[0] == 0x08:
            c4h10_concentration = (data[1] << 8) | data[2]
            if 0 <= c4h10_concentration <= 20000:  # 0~20000ppm 범위 내 값만 수용
                return c4h10_concentration
            else:
                print(f"Error: Abnormally high concentration value: {c4h10_concentration}")
                return None
        else:
            print("Error: Invalid header byte")
            return None
    except Exception as e:
        print(f"Error reading from sensor: {e}")
        return None

def show_toast(message, duration=3):
    global toast_end_time, toast_message
    toast_message = message
    toast_end_time = time.time() + duration
    toast_text.set_text(message)

def reset_measurement():
    global ipa_data, ethanol_data, current_data, measuring_ipa, measuring, waiting_for_drop, waiting_for_injection, start_time
    ipa_data = []
    ethanol_data = []
    current_data = []
    measuring_ipa = True
    measuring = False
    waiting_for_drop = False
    waiting_for_injection = False
    start_time = None
    show_toast("재시작되었습니다. IPA 가스를 주입하세요.", 5)
    reset_button.pack_forget()  # 재시작 버튼 숨기기

def update(frame):
    global measuring, start_time, measuring_ipa, toast_end_time, toast_message, waiting_for_drop, waiting_for_injection
    sensor_data = read_sensor_data()
    current_time = time.time()
    
    if toast_end_time and current_time > toast_end_time:
        toast_text.set_text('')  # 토스트 메시지 숨기기
        toast_end_time = None

    if sensor_data is not None:
        if not measuring and not waiting_for_drop and not waiting_for_injection:
            if sensor_data > 210:
                # 측정 시작
                measuring = True
                start_time = time.time()
                show_toast("측정 시작", 3)
                print(f"Measurement started at {start_time}, Concentration: {sensor_data}")
            else:
                show_toast("IPA 가스를 주입하세요.", 3)
        if measuring:
            elapsed_time = time.time() - start_time
            elapsed_time_text.set_text(f'경과 시간: {int(elapsed_time)} 초')
            current_data.append(sensor_data)
            if measuring_ipa:
                ipa_data.append(sensor_data)
                xdata_ipa = list(range(len(ipa_data)))
                line_ipa.set_data(xdata_ipa, ipa_data)
            else:
                ethanol_data.append(sensor_data)
                xdata_ethanol = list(range(len(ethanol_data)))
                line_ethanol.set_data(xdata_ethanol, ethanol_data)
            
            if elapsed_time >= 60:
                # 60초 측정 후 데이터 비교
                print("60초 측정 완료.")
                show_toast("60초 측정 완료", 5)
                measuring = False  # 측정 종료
                gas_type = predict_gas_type(current_data)
                show_toast(f"{gas_type} 측정 완료", 5)
                current_data.clear()  # 현재 측정 데이터 초기화
                
                if measuring_ipa:
                    show_toast("IPA 측정 완료. 에탄올로 전환하고 가스 농도가 떨어질 때까지 기다리세요.", 5)
                    print("IPA 측정 완료. 에탄올로 전환하고 가스 농도가 떨어질 때까지 기다리세요.")
                    measuring_ipa = False
                    waiting_for_drop = True  # 가스 농도 감소 대기 시작
                else:
                    show_toast("에탄올 측정 완료.", 5)
                    print("에탄올 측정 완료.")
                    measuring_ipa = True
                    waiting_for_drop = True  # 가스 농도 감소 대기 시작
                    show_reset_button()  # 모든 측정 완료 후 재시작 버튼 표시

        elif waiting_for_drop:
            # 가스 농도가 기준 값 이하로 떨어질 때까지 대기
            show_toast(f"가스 농도: {sensor_data} ppm. 기다려 주세요.", 3)
            if sensor_data <= 210:
                print("가스 농도가 떨어졌습니다. 가스를 주입하세요.")
                show_toast("가스를 주입하세요.", 3)
                waiting_for_drop = False
                waiting_for_injection = True  # 가스 주입 대기 시작
                start_time = time.time()  # 3초 대기 시작 시간 기록

        elif waiting_for_injection:
            elapsed_time = time.time() - start_time
            show_toast(f"가스 농도: {sensor_data} ppm. 가스를 주입하세요.", 3)
            if elapsed_time >= 3 and sensor_data > 210:
                print("가스 주입 후 측정 시작")
                show_toast("측정 시작", 3)
                measuring = True
                waiting_for_injection = False  # 가스 주입 대기 종료
                start_time = time.time()  # 측정 시작 시간 기록

    return line_ipa, line_ethanol, elapsed_time_text, toast_text

    # 재시작 버튼 추가
    root = tk.Tk()
    root.title("IR 가스 센서 데이터 측정")
    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack()

reset_button = Button(root, text="재시작", command=reset_measurement)
reset_button.pack_forget()  # 초기에는 버튼을 숨김

def show_reset_button():
    reset_button.pack(side=tk.BOTTOM, pady=10)

# matplotlib figure를 tkinter 창에 포함시키기
ani = FuncAnimation(fig, update, init_func=init, blit=True, interval=1000, save_count=120)

root.mainloop()

               
