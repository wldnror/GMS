import tkinter as tk
from tkinter import messagebox
from threading import Thread
import numpy as np
from smbus2 import SMBus
import time
import csv
import os

# I2C 버스 번호 및 주소
BUS_NUMBER = 1
DEVICE_ADDRESS = 0x54
bus = SMBus(BUS_NUMBER)

# 센서 데이터 읽기 함수
def read_sensor_data():
    try:
        bus.write_byte(DEVICE_ADDRESS, 0x52)
        time.sleep(0.1)
        data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 7)
        if data[0] == 0x08:
            concentration = (data[1] << 8) | data[2]
            if 0 <= concentration <= 20000:
                return concentration
            else:
                return None
        else:
            return None
    except Exception as e:
        print(f"센서 읽기 오류: {e}")
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
            time.sleep(1)  # 1초 간격으로 데이터 수집
        if len(sample_data) == time_steps:
            data_list.append([label] + sample_data)
            collected_samples += 1
            progress.set(f"수집 중: {collected_samples}/{samples} 샘플 완료")
            print(f"{collected_samples}/{samples} 샘플 수집 완료")
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
    gas_type = gas_type_var.get()
    concentration = concentration_var.get()
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

# GUI 루프 시작
root.mainloop()
