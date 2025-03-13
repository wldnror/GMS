import Adafruit_ADS1x15

# 사용 가능한 ADS1115 주소
ADS1115_ADDRESSES = [0x48, 0x49, 0x4A, 0x4B]

# 게인 설정 (2/3는 6.144V full-scale range)
GAIN = 2/3

# I2C 버스 번호
BUS_NUM = 1

# 각 주소의 ADS1115에 대해 채널 데이터 읽기
for address in ADS1115_ADDRESSES:
    try:
        adc = Adafruit_ADS1x15.ADS1115(address=address, busnum=BUS_NUM)
        print(f"\nScanning ADS1115 at address 0x{address:02X}")

        for channel in range(4):
            value = adc.read_adc(channel, gain=GAIN)
            voltage = value * 6.144 / 32767  # 변환된 전압 (6.144V FS)
            current = voltage / 250  # 250Ω 션트 저항 기준 전류 계산
            milliamp = current * 1000  # mA 변환
            print(f"  Channel {channel}: {milliamp:.2f} mA")

    except Exception as e:
        print(f"  Failed to read from ADS1115 at 0x{address:02X}: {e}")
