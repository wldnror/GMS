import smbus
import time
import matplotlib.pyplot as plt

# I2C 버스 번호 (라즈베리 파이 모델에 따라 다를 수 있음)
BUS_NUMBER = 1

# I2C 주소 (i2cdetect로 확인한 값)
DEVICE_ADDRESS = 0xXX  # 여기에 실제 센서 주소를 입력하세요.

# I2C 버스 초기화
bus = smbus.SMBus(BUS_NUMBER)

def read_sensor_data():
    try:
        # 센서로부터 데이터 읽기
        # 여기에 센서에 맞는 읽기 명령어를 사용하세요.
        # 예시: bus.read_byte_data(DEVICE_ADDRESS, register)
        data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 2)
        return data
    except Exception as e:
        print(f"Error reading from sensor: {e}")
        return None

# 데이터 저장 리스트
data_list = []

# 데이터 수집 시간 (초)
duration = 60
start_time = time.time()

while time.time() - start_time < duration:
    sensor_data = read_sensor_data()
    if sensor_data:
        # 센서 데이터 처리
        # 예시: 가스 농도 계산
        gas_concentration = sensor_data[0] << 8 | sensor_data[1]
        data_list.append(gas_concentration)
    
    time.sleep(1)

# 데이터 시각화
plt.plot(data_list)
plt.title("IR Gas Sensor Data")
plt.xlabel("Time (s)")
plt.ylabel("Gas Concentration")
plt.show()
