# import Adafruit_ADS1x15

# GAIN = 2/3
# adc = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)

# for channel in range(4):
#     value = adc.read_adc(channel, gain=GAIN)
#     voltage = value * 6.144 / 32767
#     current = voltage / 250
#     milliamp = current * 1000
#     print(f"Channel {channel}: {milliamp:.2f} mA")

import Adafruit_ADS1x15
import time

GAIN = 2/3
adc = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)

def read_channel(channel, gain, samples=10):
    total = 0
    for _ in range(samples):
        total += adc.read_adc(channel, gain=gain)
        time.sleep(0.01)
    return total / samples

for channel in range(4):
    value = read_channel(channel, GAIN)
    voltage = value * 6.144 / 32767
    current = voltage / 250
    milliamp = current * 1000
    print(f"Channel {channel}: {milliamp:.2f} mA")
