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
ipa_data = []
ethanol_data = []

# 현재 측정 중인 데이터 타입 (True for IPA, False for Ethanol)
measuring_ipa = True
measuring = False  # 측정 시작 여부
start_time = None  # 측정 시작 시간

# 초기화 그래프
fig, ax = plt.subplots()
line_ipa, = ax.plot([], [], lw=2, label="IPA", color='blue')
line_ethanol, = ax.plot([], [], lw=2, label="Ethanol", color='red')
ax.set_xlim(0, 60)  # x축 범위 (시간)
ax.set_ylim(0, 10000)  # y축 범위 (센서 데이터 값 범위, 예시로 0-5000 ppm 설정)
ax.set_title("IR Gas Sensor Data")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Gas Concentration (ppm)")
ax.legend()

# 실시간 데이터 업데이트 함수
def init():
    line_ipa.set_data([], [])
    line_ethanol.set_data([], [])
    return line_ipa, line_ethanol

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
            if 0 <= c4h10_concentration <= 10000:  # 0~5000ppm 범위 내 값만 수용
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

def update(frame):
    global measuring, start_time, measuring_ipa
    sensor_data = read_sensor_data()
    if sensor_data is not None:
        if not measuring and sensor_data > 210:
            # 측정 시작
            measuring = True
            start_time = time.time()
            print(f"Measurement started at {start_time}, Concentration: {sensor_data}")

        if measuring:
            elapsed_time = time.time() - start_time
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
                print("60-second measurement completed.")
                measuring = False  # 측정 종료
                
                if measuring_ipa:
                    print("IPA measurement completed. Please switch to Ethanol and wait for gas concentration to drop.")
                    measuring_ipa = False
                else:
                    print("Ethanol measurement completed.")
                    measuring_ipa = True
                
        elif sensor_data <= 210 and not measuring_ipa:
            print("Gas concentration dropped. Ready for the next measurement cycle.")

    return line_ipa, line_ethanol

ani = FuncAnimation(fig, update, init_func=init, blit=True, interval=1000, save_count=60)

plt.show()
