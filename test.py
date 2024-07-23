import tkinter as tk
from tkinter import messagebox
from threading import Thread
import numpy as np
from smbus2 import SMBus
import time
import csv
import os
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# I2C 버스 번호 및 주소
BUS_NUMBER = 1
DEVICE_ADDRESS = 0x54
bus = SMBus(BUS_NUMBER)

time_steps = 60  # 전역 변수로 선언
measuring = False  # 데이터 수집 중인지 여부
current_values = []  # 전역 변수로 이동
current_times = []  # 시간을 저장할 리스트
time_interval = 3  # 3초 간격

# I2C 버스 재설정 함수
def reset_i2c_bus():
    try:
        bus.close()
        time.sleep(0.5)
        bus.open(BUS_NUMBER)
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

# 실시간 센서 데이터 출력 함수
def print_sensor_data():
    global current_times, current_values
    last_data = None
    while True:
        data = read_sensor_data()
        if data is not None:
            print(f"실시간 가스 농도: {data} ppm")
            if data == 0:
                current_values.clear()
                current_times.clear()
                last_data = None
            else:
                if len(current_values) >= time_steps:
                    current_values.pop(0)
                    current_times.pop(0)
                current_values.append(data)
                if data != last_data:
                    elapsed_time = len(current_times) * time_interval
                    current_times.append(elapsed_time)
                    last_data = data
                else:
                    current_times.append(current_times[-1] if current_times else 0)
        time.sleep(3)  # 3초 간격으로 데이터 수집

# 데이터 수집 함수
def collect_data(filename, label, samples=100, time_steps=60):
    global measuring
    data_list = []
    collected_samples = 0

    if os.path.exists(filename):
        with open(filename, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if int(row[0]) == label:
                    collected_samples += 1

    for i in range(collected_samples, samples):
        sample_data = []
        while True:
            initial_data = read_sensor_data()
            if initial_data is not None and initial_data >= 250:
                break
            progress.set(f"수집 대기 중: 가스 농도 {initial_data} ppm")
            time.sleep(3)  # 3초 간격으로 데이터 수집
            
        last_data = None
        for j in range(time_steps):
            data = read_sensor_data()
            if data is not None:
                sample_data.append(data)
                if data == 0:
                    current_values.clear()
                    current_times.clear()
                    last_data = None
                else:
                    if len(current_values) >= time_steps:
                        current_values.pop(0)
                        current_times.pop(0)
                    current_values.append(data)
                    if data != last_data:
                        elapsed_time = len(current_times) * time_interval
                        current_times.append(elapsed_time)
                        last_data = data
                    else:
                        current_times.append(current_times[-1] if current_times else 0)
                progress.set(f"수집 중: 샘플 {i+1}/{samples}, 데이터 포인트 {j+1}/{time_steps}, 현재 값: {data} ppm")
            else:
                print(f"데이터 포인트 읽기 실패: 샘플 {i+1}, 포인트 {j+1}")
                progress.set(f"수집 실패: 샘플 {i+1}/{samples}, 데이터 포인트 {j+1}/{time_steps}")
                break
            time.sleep(3)  # 3초 간격으로 데이터 수집
        if len(sample_data) == time_steps:
            data_list.append([label] + sample_data)
            collected_samples += 1
            progress.set(f"수집 중: {collected_samples}/{samples} 샘플 완료")
            print(f"{collected_samples}/{samples} 샘플 수집 완료")
            if not messagebox.askokcancel("다음 샘플로 이동", f"다음 샘플로 이동하시겠습니까?\n마지막 값: {sample_data[-1]} ppm"):
                break
        else:
            print(f"{collected_samples}/{samples} 샘플 수집 실패")
    
    if data_list:
        with open(filename, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(data_list)
    
    messagebox.showinfo("완료", f"{samples}개의 샘플 수집 완료!")
    progress.set("수집 완료")
    measuring = False
    start_button.config(state=tk.NORMAL)

# 데이터 수집 시작 함수
def start_collection():
    global measuring
    if measuring:
        return
    measuring = True
    start_button.config(state=tk.DISABLED)
    
    global time_steps
    gas_type = gas_type_var.get()
    concentration = concentration_var.get()
    time_steps = 60
    if gas_type and concentration:
        filename = f"{gas_type.lower()}_data.csv"
        label_map = {
            "에탄올 100%": 0,
            "에탄올 20%": 1,
            "에탄올 10%": 2,
            "에탄올 표준": 6,
            "IPA 100%": 3,
            "IPA 20%": 4,
            "IPA 10%": 5,
            "IPA 표준": 7
        }
        label = label_map.get(f"{gas_type} {concentration}%")
        if label is None:
            label = label_map.get(f"{gas_type} 표준")
        progress.set("수집 시작")
        collection_thread = Thread(target=collect_data, args=(filename, label))
        collection_thread.start()
    else:
        messagebox.showwarning("입력 오류", "가스 종류와 농도를 선택하세요.")
        measuring = False
        start_button.config(state=tk.NORMAL)

# 실시간 그래프 업데이트 함수
def update_graph(frame):
    if len(current_values) > 0:
        line.set_ydata(current_values[-time_steps:] if len(current_values) >= time_steps else current_values)
        line.set_xdata(current_times[-time_steps:] if len(current_times) >= time_steps else current_times)
        ax.relim()
        ax.autoscale_view()
    return line,

# GUI 생성
root = tk.Tk()
root.title("데이터 수집기")

# 가스 종류 선택 라벨 및 옵션 메뉴
tk.Label(root, text="가스 종류:").grid(row=0, column=0)
gas_type_var = tk.StringVar(root)
gas_type_options = ["에탄올", "IPA"]
gas_type_menu = tk.OptionMenu(root, gas_type_var, *gas_type_options)
gas_type_menu.grid(row=0, column=1)

# 농도 선택 라벨 및 옵션 메뉴
tk.Label(root, text="농도:").grid(row=1, column=0)
concentration_var = tk.StringVar(root)
concentration_options = ["100", "20", "10", "표준"]
concentration_menu = tk.OptionMenu(root, concentration_var, *concentration_options)
concentration_menu.grid(row=1, column=1)

# 데이터 수집 시작 버튼
start_button = tk.Button(root, text="수집 시작", command=start_collection)
start_button.grid(row=2, columnspan=2)

# 진행 상황 라벨
progress = tk.StringVar()
progress.set("대기 중")
tk.Label(root, textvariable=progress).grid(row=3, columnspan=2)

# 실시간 그래프 표시
fig, ax = plt.subplots()
ax.set_ylim(0, 18000)  # 그래프의 y축 범위 설정
line, = ax.plot([], [], lw=2)
canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().grid(row=4, columnspan=2)
ani = FuncAnimation(fig, update_graph, interval=3000, cache_frame_data=False)  # 3초 간격으로 업데이트

# 실시간 센서 데이터 출력 스레드 시작
sensor_thread = Thread(target=print_sensor_data)
sensor_thread.daemon = True
sensor_thread.start()

# GUI 루프 시작
root.mainloop()
