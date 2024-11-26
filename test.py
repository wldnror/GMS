import time
import board
import busio
import adafruit_ads1x15.ads1015 as ADS  # 모듈을 ADS로 임포트
from adafruit_ads1x15.analog_in import AnalogIn

# I2C 설정
i2c = busio.I2C(board.SCL, board.SDA)

# ADS1015 객체 생성 및 PGA 설정 (예: gain=2/3 => +/-6.144V)
ads = ADS.ADS1015(i2c, gain=2/3)

# 채널 설정 (AIN0에 연결)
chan = AnalogIn(ads, ADS.P0)  # ADS.P0 사용

# 변환 공식 (0V ~ 5V => 0 kPa ~ 2.5 kPa)
def convert_to_pressure(voltage):
    return voltage * 500  # V당 500 Pa

# 여러 번 측정하여 평균을 구하는 함수
def get_average_voltage(channel, samples=10, delay=0.01):
    total = 0.0
    for _ in range(samples):
        total += channel.voltage
        time.sleep(delay)  # 각 샘플 사이의 지연 (초)
    average = total / samples
    return average

# 무한 루프
try:
    print("프로그램을 시작합니다. 종료하려면 Ctrl+C를 누르세요.")
    while True:
        raw_adc = chan.value  # 원시 ADC 값
        voltage = get_average_voltage(chan, samples=10, delay=0.01)  # 평균 전압 계산
        pressure = convert_to_pressure(voltage)  # 전압을 공기압으로 변환
        print(f"Raw ADC: {raw_adc}, Voltage: {voltage:.2f} V, Pressure: {pressure:.2f} Pa")
        time.sleep(1)
except KeyboardInterrupt:
    print("\n프로그램을 종료합니다.")
except Exception as e:
    print(f"오류 발생: {e}")
