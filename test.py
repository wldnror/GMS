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
chan = AnalogIn(ads, ADS.P0)  # ADS.P0로 수정

# 변환 공식 (센서 데이터시트 참조)
def convert_to_pressure(voltage):
    # 예: 전압을 공기압으로 변환 (수식은 센서 스펙에 따라 조정 필요)
    return voltage * 10  # 임시 수식

# 무한 루프
while True:
    voltage = chan.voltage  # 센서에서 읽은 전압 (V)
    pressure = convert_to_pressure(voltage)  # 전압을 공기압으로 변환
    print(f"Voltage: {voltage:.2f} V, Pressure: {pressure:.2f} Pa")
    time.sleep(1)
