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
toast_end_time = None  # 토스트 메시지 종료 시간
toast_message = ""  # 토스트 메시지 내용

# 초기화 그래프
fig, ax = plt.subplots()
line_ipa, = ax.plot([], [], lw=2, label="IPA", color='blue')
line_ethanol, = ax.plot([], [], lw=2, label="Ethanol", color='red')
ax.set_xlim(0, 60)  # x축 범위 (시간)
ax.set_ylim(0, 5000)  # y축 범위 (센서 데이터 값 범위, 예시로 0-5000 ppm 설정)
ax.set_title("IR Gas Sensor Data")
ax.set_xlabel("Time (s)")
ax.set_ylabel("Gas Concentration (ppm)")
ax.legend()

# 시간 표시 추가
elapsed_time_text = ax.text(0.95, 0.95, '', transform=ax.transAxes, ha='right', va='top', fontsize=12)
# 토스트 메시지 표시 추가
toast_text = ax.text(0.5, 0.1, '', transform=ax.transAxes, ha='center', va='center', fontsize=12, bbox=dict(facecolor='red', alpha=0.5))

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
            if 0 <= c4h10_concentration <= 5000:  # 0~5000ppm 범위 내 값만 수용
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

def update(frame):
    global measuring, start_time, measuring_ipa, toast_end_time, toast_message
    sensor_data = read_sensor_data()
    current_time = time.time()
    
    if toast_end_time and current_time > toast_end_time:
        toast_text.set_text('')  # 토스트 메시지 숨기기
        toast_end_time = None

    if sensor_data is not None:
        if not measuring and sensor_data > 210:
            # 측정 시작
            measuring = True
            start_time = time.time()
            show_toast("Measurement started", 3)
            print(f"Measurement started at {start_time}, Concentration: {sensor_data}")

        if measuring:
            elapsed_time = time.time() - start_time
            elapsed_time_text.set_text(f'Time: {int(elapsed_time)} s')
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
                show_toast("60-second measurement completed", 5)
                measuring = False  # 측정 종료
                
                if measuring_ipa:
                    show_toast("IPA measurement completed. Please switch to Ethanol and wait for gas concentration to drop.", 5)
                    print("IPA measurement completed. Please switch to Ethanol and wait for gas concentration to drop.")
                    measuring_ipa = False
                else:
                    show_toast("Ethanol measurement completed.", 5)
                    print("Ethanol measurement completed.")
                    measuring_ipa = True

        elif not measuring and not measuring_ipa:
            # 가스 농도가 기준 값 이하로 떨어질 때까지 대기
            if sensor_data <= 210:
                print("Gas concentration dropped. Ready for the next measurement cycle.")
                show_toast("Gas concentration dropped. Ready for the next measurement cycle.", 3)
                measuring = True  # 측정 시작
                start_time = time.time()
                print(f"Measurement started at {start_time}, Concentration: {sensor_data}")

    return line_ipa, line_ethanol, elapsed_time_text, toast_text

ani = FuncAnimation(fig, update, init_func=init, blit=True, interval=1000, save_count=120)

plt.show()
