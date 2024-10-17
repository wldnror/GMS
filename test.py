import Adafruit_ADS1x15

adc = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)  # busnum=1 추가
GAIN = 1  # 게인 설정

# 채널 0에서 값 읽기

value_ch1 = adc.read_adc(1, gain=GAIN)
value_ch2 = adc.read_adc(2, gain=GAIN)
value_ch3 = adc.read_adc(3, gain=GAIN)
print(f'Channel 1: {value_ch1}')
print(f'Channel 2: {value_ch2}')
print(f'Channel 3: {value_ch3}')
