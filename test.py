import time
import board
import busio
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# I2C 설정
i2c = busio.I2C(board.SCL, board.SDA)

# ADS1015 객체 생성 및 PGA 설정 (gain=2/3 => ±6.144V)
ads = ADS.ADS1015(i2c, gain=2/3)

# 채널 설정 (AIN0에 연결)
chan = AnalogIn(ads, ADS.P0)

# 변환 공식 (0V ~ 5V => 0 kPa ~ 2.5 kPa)
def convert_to_pressure(voltage):
    return voltage * 500  # V당 500 Pa

# 평균화 함수
def get_average_voltage(channel, samples=10):
    total = 0.0
    for _ in range(samples):
        total += channel.voltage
        time.sleep(0.01)  # 10ms 간격
    return total / samples

# 무한 루프
try:
    while True:
        voltage = get_average_voltage(chan, samples=10)  # 10번 읽고 평균
        pressure = convert_to_pressure(voltage)
        print(f"Voltage: {voltage:.2f} V, Pressure: {pressure:.2f} Pa")
        time.sleep(1)
except KeyboardInterrupt:
    print("프로그램을 종료합니다.")
except Exception as e:
    print(f"오류 발생: {e}")
