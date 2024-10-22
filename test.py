import smbus2
import time

# I2C 버스 초기화 (Raspberry Pi에서는 bus 1 사용)
bus = smbus2.SMBus(1)

# ADS1115 기본 주소
ADS1115_ADDRESS = 0x48
ADS1115_CONVERSION_REG = 0x00
ADS1115_CONFIG_REG = 0x01

# ADS1115 설정 값
CONFIG_START_SINGLE = 0x8000
CONFIG_GAIN_2_3 = 0x0000  # ±6.144V 범위
CONFIG_MODE_SINGLE = 0x0100
CONFIG_DR_1600SPS = 0x0080  # 1600 샘플링 속도
CONFIG_COMP_MODE_TRAD = 0x0000
CONFIG_COMP_POL_LOW = 0x0000
CONFIG_COMP_LAT_NON = 0x0000
CONFIG_COMP_QUE_DISABLE = 0x0003

# 각 채널에 대한 MUX 설정 값
CONFIG_MUX = {
    'AIN0': 0x4000,  # AIN0 입력
    'AIN1': 0x5000,  # AIN1 입력
    'AIN2': 0x6000,  # AIN2 입력
    'AIN3': 0x7000   # AIN3 입력
}

# CONFIG 레지스터에 설정 값을 쓰기
def write_config(channel):
    config = (
        CONFIG_START_SINGLE |
        CONFIG_MUX[channel] |
        CONFIG_GAIN_2_3 |
        CONFIG_MODE_SINGLE |
        CONFIG_DR_1600SPS |
        CONFIG_COMP_MODE_TRAD |
        CONFIG_COMP_POL_LOW |
        CONFIG_COMP_LAT_NON |
        CONFIG_COMP_QUE_DISABLE
    )
    config_high = (config >> 8) & 0xFF
    config_low = config & 0xFF
    bus.write_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONFIG_REG, [config_high, config_low])

# 변환된 값을 읽기
def read_conversion():
    data = bus.read_i2c_block_data(ADS1115_ADDRESS, ADS1115_CONVERSION_REG, 2)
    raw_value = (data[0] << 8) | data[1]
    
    # 음수 값 처리 (2의 보수 표현)
    if raw_value > 0x7FFF:
        raw_value -= 0x10000
    
    return raw_value

# 전류 값 계산 (4-20mA 변환)
def calculate_current(raw_value):
    voltage = raw_value * 6.144 / 32767  # ±6.144V 범위로 변환
    current = voltage / 250  # 250옴 저항 사용 시 전류 계산
    milliamp = current * 1000  # mA 단위로 변환
    return milliamp

# 테스트 루프 (4개의 채널)
def main():
    try:
        while True:
            for channel in ['AIN0', 'AIN1', 'AIN2', 'AIN3']:
                write_config(channel)  # 해당 채널로 설정을 쓰기
                time.sleep(0.05)  # 변환 대기 시간을 늘림 (50ms)
                raw_value = read_conversion()  # 변환된 값 읽기
                milliamp = calculate_current(raw_value)  # mA로 변환
                print(f"Channel {channel} Current: {milliamp:.2f} mA")
            time.sleep(1)  # 1초 대기 후 다음 샘플링
    except KeyboardInterrupt:
        print("테스트 종료")
    finally:
        bus.close()

if __name__ == "__main__":
    main()
