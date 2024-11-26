# import Adafruit_ADS1x15

# GAIN = 2 / 3
# addresses = [0x48, 0x49, 0x4A, 0x4B]

# for addr in addresses:
#     print(f"\nScanning ADS1115 at address 0x{addr:02X}")
#     try:
#         adc = Adafruit_ADS1x15.ADS1115(address=addr, busnum=1)
#         for channel in range(4):
#             try:
#                 value = adc.read_adc(channel, gain=GAIN)
#                 voltage = value * 6.144 / 32767
#                 current = voltage / 250  # Assuming a 250 ohm shunt resistor
#                 milliamp = current * 1000
#                 if milliamp < 0.01:  # Threshold to detect no connection
#                     print(f"Channel {channel}: 미연결")
#                 else:
#                     print(f"Channel {channel}: {milliamp:.2f} mA")
#             except Exception as e:
#                 print(f"Channel {channel}: Error reading - {e}")
#     except Exception as e:
#         print(f"Error initializing ADS1115 at 0x{addr:02X} - {e}")


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
chan = AnalogIn(ads, ADS1015.P0)  # ADS1015 대신 ADS로 수정

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
