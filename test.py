import Adafruit_ADS1x15

GAIN = 2/3
adc = Adafruit_ADS1x15.ADS1115(address=0x4A, busnum=1)
 # 0x4A, 0x4B

for channel in range(4):
    value = adc.read_adc(channel, gain=GAIN)
    voltage = value * 6.144 / 32767
    current = voltage / 250
    milliamp = current * 1000
    print(f"Channel {channel}: {milliamp:.2f} mA")
