import Adafruit_ADS1x15

adc = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)  # busnum=1 추가
GAIN = 1  # 게인 설정

# 채널 0에서 값 읽기
value = adc.read_adc(0, gain=GAIN)
print(f'Channel 0: {value}')
