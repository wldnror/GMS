import smbus
import time

# I2C 버스 및 ADS1115 주소
bus = smbus.SMBus(1)
address = 0x48

# ADS1115 설정 레지스터 (0x01)에 데이터를 쓰기
config = [0xC2, 0x83]  # 적절한 설정값
bus.write_i2c_block_data(address, 0x01, config)

# 컨버전 레지스터 (0x00)에서 데이터를 읽기
time.sleep(0.5)  # 변환 대기 시간
data = bus.read_i2c_block_data(address, 0x00, 2)

# 데이터 변환
raw_adc = (data[0] << 8) | data[1]
if raw_adc > 32767:
    raw_adc -= 65535

print(f"ADC Value: {raw_adc}")
