from smbus2 import SMBus
import time

# I2C 버스 및 ADS1115 주소
bus = SMBus(1)
address = 0x48

# ADS1115 설정값(각 채널 설정)
configs = {
    0: [0xC2, 0x83],  # 채널 0 설정 (예시)
    1: [0xD2, 0x83],  # 채널 1 설정
    2: [0xE2, 0x83],  # 채널 2 설정
    3: [0xF2, 0x83],  # 채널 3 설정
}

# 채널별 데이터를 읽고 변환하는 함수
def read_adc(channel):
    config = configs[channel]
    
    # 해당 채널로 설정 레지스터 쓰기
    bus.write_i2c_block_data(address, 0x01, config)
    
    # 약간의 대기 시간 (변환 대기)
    time.sleep(0.1)
    
    # 컨버전 레지스터에서 2바이트 읽기
    data = bus.read_i2c_block_data(address, 0x00, 2)

    # 데이터 변환 (2바이트 -> 16비트 값)
    raw_adc = (data[0] << 8) | data[1]
    if raw_adc > 32767:
        raw_adc -= 65535

    return raw_adc

# 4~20mA 변환 함수
def convert_to_current(adc_value):
    # ADC 값을 4~20mA로 변환 (예시로 0~32767을 4~20mA로 매핑)
    current = (adc_value / 32767.0) * (20 - 4) + 4
    return current

# 실시간 모니터링
try:
    while True:
        for channel in range(4):
            adc_value = read_adc(channel)
            current_value = convert_to_current(adc_value)
            print(f"Channel {channel} ADC: {adc_value}, Current: {current_value:.2f} mA")

        time.sleep(1)  # 1초 간격으로 업데이트

except KeyboardInterrupt:
    print("실시간 모니터링 중단")
