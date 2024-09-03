import smbus2
import time

# I2C 버스 설정 (라즈베리 파이에서는 보통 1번 버스 사용)
I2C_BUS = 1
INA219_ADDRESS = 0x40  # INA219 기본 주소

# 레지스터 주소
INA219_REG_BUS_VOLTAGE = 0x02  # 전압 측정 레지스터
INA219_REG_SHUNT_VOLTAGE = 0x01  # 션트 전압 측정 레지스터

# I2C 버스 초기화
bus = smbus2.SMBus(I2C_BUS)

def read_voltage():
    # 버스 전압 읽기
    raw_voltage = bus.read_word_data(INA219_ADDRESS, INA219_REG_BUS_VOLTAGE)
    # 데이터 순서 조정 (리틀 엔디안 -> 빅 엔디안)
    raw_voltage = ((raw_voltage & 0xFF) << 8) | (raw_voltage >> 8)
    # 비트 쉬프트 후 전압 변환 (LSB 당 4mV)
    voltage = (raw_voltage >> 3) * 4 * 0.001
    return voltage

def read_current():
    # 션트 전압 읽기
    raw_shunt_voltage = bus.read_word_data(INA219_ADDRESS, INA219_REG_SHUNT_VOLTAGE)
    # 데이터 순서 조정 (리틀 엔디안 -> 빅 엔디안)
    raw_shunt_voltage = ((raw_shunt_voltage & 0xFF) << 8) | (raw_shunt_voltage >> 8)
    # 션트 전압 변환 (LSB 당 10uV, 저항값에 따라 조정 필요)
    current = raw_shunt_voltage * 0.01  # mA 단위로 출력 (저항값 0.1옴 기준)
    return current

try:
    while True:
        voltage = read_voltage()
        current = read_current()
        print(f"Bus Voltage: {voltage:.2f} V")
        print(f"Current: {current:.2f} mA")
        time.sleep(1)

except KeyboardInterrupt:
    print("종료")
    bus.close()
