import time
import board
import busio
from adafruit_ads1x15.ads1015 import ADS1015
from adafruit_ads1x15.analog_in import AnalogIn

# I2C 설정
i2c = busio.I2C(board.SCL, board.SDA)

# ADS1015 객체 생성
ads = ADS1015(i2c)

# 채널 설정 (AIN0에 연결)
chan = AnalogIn(ads, ADS1015.P0)  # ADS1015.P0 → ads.P0으로 수정 필요 없음
