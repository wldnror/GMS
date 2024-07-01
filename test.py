import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from smbus2 import SMBus

# I2C 버스 번호
BUS_NUMBER = 1

# I2C 주소
DEVICE_ADDRESS = 0x54

# I2C 버스 초기화
bus = SMBus(BUS_NUMBER)

# 데이터 저장 리스트
data_list = []

# 초기화 그래프
fig, ax = plt.subplots()
line, = ax.plot([], [], lw=2)
ax.set_xlim(0, 60)  # x축 범위 (시간)
ax.set_ylim(0, 5000)  # y축 범위 (센서 데이터 값 범위, 예시로 0-5000 ppm 설정)
ax.set_title("IR Gas Sensor Data")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Gas Concentration (ppm)")

# 실시간 데이터 업데이트 함수
def init():
    line.set_data([], [])
    return line,

def read_sensor_data():
    try:
        # 12 바이트 데이터 읽기 (ASCII 형식)
        data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 12)
        
        # ASCII 데이터 해석
        data_str = ''.join(chr(byte) for byte in data)
        print(f"Raw data: {data_str}")
        
        # 가스 농도 값 추출
        if "ppm" in data_str:
            concentration_str = data_str.split(' ')[1]
            gas_concentration = int(concentration_str.replace(',', ''))
            return gas_concentration
        else:
            print("Error: 'ppm' string not found in data")
            return None
    except Exception as e:
        print(f"Error reading from sensor: {e}")
        return None

def update(frame):
    sensor_data = read_sensor_data()
    if sensor_data is not None:
        data_list.append(sensor_data)
        print(f"Gas concentration: {sensor_data}")

        # x축 범위 조정
        xdata = list(range(len(data_list)))
        line.set_data(xdata, data_list)

        # x축 범위를 데이터 길이에 따라 조정
        ax.set_xlim(max(0, len(data_list) - 60), len(data_list))
        
    return line,

ani = FuncAnimation(fig, update, init_func=init, blit=True, interval=1000)

plt.show()
