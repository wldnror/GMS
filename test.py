import Adafruit_ADS1x15

GAIN = 2 / 3
addresses = [0x48, 0x49, 0x4A, 0x4B]

for addr in addresses:
    print(f"\nScanning ADS1115 at address 0x{addr:02X}")
    try:
        adc = Adafruit_ADS1x15.ADS1115(address=addr, busnum=1)
        for channel in range(4):
            try:
                value = adc.read_adc(channel, gain=GAIN)
                voltage = value * 6.144 / 32767
                current = voltage / 250  # Assuming a 250 ohm shunt resistor
                milliamp = current * 1000
                if milliamp < 0.01:  # Threshold to detect no connection
                    print(f"Channel {channel}: 미연결")
                else:
                    print(f"Channel {channel}: {milliamp:.2f} mA")
            except Exception as e:
                print(f"Channel {channel}: Error reading - {e}")
    except Exception as e:
        print(f"Error initializing ADS1115 at 0x{addr:02X} - {e}")
