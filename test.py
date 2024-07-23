import time
import Adafruit_ADS1x15
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# 각 ADS1115 모듈에 대한 인스턴스 생성
adc1 = Adafruit_ADS1x15.ADS1115(address=0x48)
adc2 = Adafruit_ADS1x15.ADS1115(address=0x49)
adc3 = Adafruit_ADS1x15.ADS1115(address=0x4A)
adc4 = Adafruit_ADS1x15.ADS1115(address=0x4B)

# ADS1115의 게인 설정 (2/3 -> +/-6.144V 범위)
GAIN = 2/3

# 각 모듈에서 아날로그 입력 읽기 (여기서는 단일 엔드 모드로 읽음)
def read_adc(adc):
    values = []
    for i in range(4):
        value = adc.read_adc(i, gain=GAIN)
        voltage = value * 6.144 / 32767  # 2/3 게인 사용시 전압 범위
        current = voltage / 250  # 저항 250Ω 사용
        values.append(current * 1000)  # mA로 변환
    return values

# 초기화
fig, axs = plt.subplots(4, 1, figsize=(10, 8))
lines = []
for i in range(4):
    line, = axs[i].plot([], [], lw=2)
    lines.append(line)
    axs[i].set_ylim(0, 18000)  # ppm 범위 설정
    axs[i].set_xlim(0, 300)  # 300초 범위 설정
    axs[i].grid()
    axs[i].set_title(f"Module {i+1} Currents (ppm)")

xdata = list(range(0, 300, 3))  # 0부터 300까지 3초 간격
ydata = [[0]*100 for _ in range(4)]

# 업데이트 함수
def update(frame):
    global ydata
    values1 = read_adc(adc1)
    values2 = read_adc(adc2)
    values3 = read_adc(adc3)
    values4 = read_adc(adc4)
    
    # 각 모듈의 데이터를 업데이트
    new_values = [values1, values2, values3, values4]
    for i in range(4):
        ydata[i] = ydata[i][1:] + [new_values[i][0]]  # 새 데이터 추가 (채널 0 데이터만 사용)
        lines[i].set_ydata(ydata[i])
        lines[i].set_xdata(xdata)

    return lines

# 애니메이션 설정
ani = animation.FuncAnimation(fig, update, frames=range(100), blit=True, interval=3000)

plt.tight_layout()
plt.show()
