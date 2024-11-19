import Adafruit_ADS1x15
import time

GAIN = 2/3
adc = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)

def read_single_channel(channel, gain):
    value = adc.read_adc(channel, gain=gain)
    voltage = value * 6.144 / 32767
    current = voltage / 250
    milliamp = current * 1000
    print(f"Channel {channel} Raw: {value}, Current: {milliamp:.2f} mA")

for channel in range(4):
    read_single_channel(channel, GAIN)
    time.sleep(0.5)  # 잠시 대기하여 안정적인 읽기
