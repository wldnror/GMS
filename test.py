import time
import board
import busio
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from collections import deque

# I2C 설정
i2c = busio.I2C(board.SCL, board.SDA)

# ADS1015 객체 생성 및 PGA 설정 (gain=1 => +/-4.096V)
ads = ADS.ADS1015(i2c, gain=1)

# 샘플링 속도 설정 (기본값은 128 SPS)
ads.data_rate = 128  # SPS: Samples Per Second

# 채널 설정 (AIN0에 연결)
chan = AnalogIn(ads, ADS.P0)

# 변환 공식 (0V ~ 5V => 0 kPa ~ 2.5 kPa)
def convert_to_pressure(voltage):
    return voltage * 500  # V당 500 Pa

# 이동 평균 필터를 위한 deque 설정
class MovingAverage:
    def __init__(self, size=10):
        self.size = size
        self.values = deque(maxlen=size)
    
    def add(self, value):
        self.values.append(value)
    
    def average(self):
        if not self.values:
            return 0
        return sum(self.values) / len(self.values)

# 이동 평균 필터 객체 생성
moving_avg = MovingAverage(size=20)

# 여러 번 측정하여 평균을 구하는 함수
def get_filtered_voltage(channel, moving_average_filter, samples=10, delay=0.005):
    total = 0.0
    for _ in range(samples):
        voltage = channel.voltage
        moving_average_filter.add(voltage)
        total += voltage
        time.sleep(delay)  # 각 샘플 사이의 지연 (초)
    average = total / samples
    filtered_average = moving_average_filter.average()
    return average, filtered_average

# 무한 루프
try:
    print("프로그램을 시작합니다. 종료하려면 Ctrl+C를 누르세요.")
    while True:
        raw_adc = chan.value  # 원시 ADC 값
        voltage_avg, voltage_filtered = get_filtered_voltage(chan, moving_avg, samples=10, delay=0.005)
        pressure_avg = convert_to_pressure(voltage_avg)
        pressure_filtered = convert_to_pressure(voltage_filtered)
        print(f"Raw ADC: {raw_adc}, Avg Voltage: {voltage_avg:.3f} V, Filtered Avg Voltage: {voltage_filtered:.3f} V, "
              f"Avg Pressure: {pressure_avg:.2f} Pa, Filtered Pressure: {pressure_filtered:.2f} Pa")
        time.sleep(1)
except KeyboardInterrupt:
    print("\n프로그램을 종료합니다.")
except Exception as e:
    print(f"오류 발생: {e}")
