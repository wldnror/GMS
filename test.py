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

# 데이터 수집 함수
def collect_data(filename, label, samples=100, time_steps=60):
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
        for j in range(time_steps):
            data = read_sensor_data()
            if data is not None:
                sample_data.append(data)
                if len(current_values) >= time_steps:
                    current_values.pop(0)
                current_values.append(data)
                progress.set(f"수집 중: 샘플 {i+1}/{samples}, 데이터 포인트 {j+1}/{time_steps}")
            else:
                print(f"데이터 포인트 읽기 실패: 샘플 {i+1}, 포인트 {j+1}")
                progress.set(f"수집 실패: 샘플 {i+1}/{samples}, 데이터 포인트 {j+1}/{time_steps}")
                break
            time.sleep(1)  # 1초 간격으로 데이터 수집
        if len(sample_data) == time_steps:
            data_list.append([label] + sample_data)
            collected_samples += 1
            progress.set(f"수집 중: {collected_samples}/{samples} 샘플 완료")
            print(f"{collected_samples}/{samples} 샘플 수집 완료")
            if not messagebox.askokcancel("다음 샘플로 이동", "다음 샘플로 이동하시겠습니까?"):
                break
        else:
            print(f"{collected_samples}/{samples} 샘플 수집 실패")
    
    if data_list:
        with open(filename, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(data_list)
    
    messagebox.showinfo("완료", f"{samples}개의 샘플 수집 완료!")
    progress.set("수집 완료")

# 데이터 수집 시작 함수
def start_collection():
    global time_steps
    gas_type = gas_type_var.get()
    concentration = concentration_var.get()
    time_steps = 60
    if gas_type and concentration:
        filename = f"{gas_type.lower()}_data.csv"
        label_map = {
            "에탄올 100%": 0,
            "에탄올 90%": 1,
            "에탄올 80%": 2,
            "IPA 100%": 3,
            "IPA 90%": 4,
            "IPA 80%": 5
        }
        label = label_map[f"{gas_type} {concentration}%"]
        progress.set("수집 시작")
        collection_thread = Thread(target=collect_data, args=(filename, label))
        collection_thread.start()
    else:
        messagebox.showwarning("입력 오류", "가스 종류와 농도를 선택하세요.")

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
concentration_options = ["100", "90", "80"]
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
current_values = []
line, = ax.plot([], [], lw=2)
canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().grid(row=4, columnspan=2)
ani = FuncAnimation(fig, update_graph, interval=1000, cache_frame_data=False)

# GUI 루프 시작
root.mainloop()
