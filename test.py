import matplotlib.pyplot as plt
import matplotlib.animation as animation
import Adafruit_ADS1x15

# ADS1115 설정
GAIN = 2/3        # full-scale 전압 6.144V 기준
adc = Adafruit_ADS1x15.ADS1115(address=0x4B, busnum=1)

# 최대 전류 (mA)를 100%로 가정 (환경에 따라 변경)
max_current = 10.0

# 그래프 설정
fig, ax = plt.subplots()
channels = [0, 1, 2, 3]
bar_container = ax.bar([f"Ch {ch}" for ch in channels], [0]*len(channels))

ax.set_ylim(0, 100)
ax.set_ylabel("Activation (%)")
ax.set_title("실시간 활성도 (0~100%)")

def read_percentages():
    percentages = []
    for channel in channels:
        value = adc.read_adc(channel, gain=GAIN)
        # ADS1115의 raw 값을 전압으로 변환
        voltage = value * 6.144 / 32767  
        # 250Ω 션트 저항 기준으로 전류 계산 (A)
        current = voltage / 250  
        milliamp = current * 1000  # mA 단위 변환
        # 최대 전류 대비 백분율 계산 (0~100% 범위로 제한)
        percent = min(max(milliamp / max_current * 100, 0), 100)
        percentages.append(percent)
    return percentages

def animate(frame):
    percentages = read_percentages()
    for bar, percent in zip(bar_container, percentages):
        bar.set_height(percent)
    return bar_container

# 1초 간격으로 업데이트
ani = animation.FuncAnimation(fig, animate, interval=1000)

plt.show()
