import Adafruit_ADS1x15

GAIN = 1
adc = Adafruit_ADS1x15.ADS1115(address=0x48, busnum=1)

channel = 0  # A1 채널만 읽기
value = adc.read_adc(channel, gain=GAIN)
voltage = value * 4.096 / 32767
current = voltage / 250
milliamp = current * 1000
print(f"Channel {channel}: {milliamp:.2f} mA")
