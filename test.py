import Adafruit_ADS1x15
import time

# GAIN 설정: ±6.144V 범위
GAIN = 2/3  # GAIN 값 조정 가능

# ADC1115 초기화 (I2C 주소 0x4A와 busnum=1 설정)
adc = Adafruit_ADS1x15.ADS1115(address=0x4A, busnum=1)

# 전류 변환 함수 (250옴 저항 기반)
def convert_to_current(voltage):
    current = voltage / 250.0 * 1000  # V = IR -> I = V/R
    return current

# 실시간 모니터링 함수
def read_and_display_adc():
    try:
        while True:
            for channel in range(4):
                # 각 채널의 ADC 값 읽기
                adc_value = adc.read_adc(channel, gain=GAIN)

                # ADC 값을 전압으로 변환 (±6.144V)
                voltage = (adc_value / 32767.0) * 6.144

                # 전압을 전류(4-20mA)로 변환
                current = convert_to_current(voltage)

                # 출력
                print(f"Channel {channel} -> ADC Value: {adc_value}, Voltage: {voltage:.2f} V, Current: {current:.2f} mA")

            time.sleep(1)  # 1초 간격으로 업데이트

    except KeyboardInterrupt:
        print("실시간 모니터링 중단")

# 메인 함수 호출
if __name__ == "__main__":
    read_and_display_adc()
