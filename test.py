import time
import Adafruit_ADS1x15

# 각 ADS1115 모듈에 대한 인스턴스 생성
adc1 = Adafruit_ADS1x15.ADS1115(address=0x48)
adc2 = Adafruit_ADS1x15.ADS1115(address=0x49)
adc3 = Adafruit_ADS1x15.ADS1115(address=0x4A)
adc4 = Adafruit_ADS1x15.ADS1115(address=0x4B)

# ADS1115의 게인 설정 (1 -> +/-4.096V 범위)
GAIN = 1

# 각 모듈에서 아날로그 입력 읽기 (여기서는 단일 엔드 모드로 읽음)
def read_adc(adc):
    values = []
    for i in range(4):
        value = adc.read_adc(i, gain=GAIN)
        voltage = value * 4.096 / 32767
        current = voltage / 250  # 저항 250Ω 사용
        values.append(current * 1000)  # mA로 변환
    return values

# 데이터 읽기 및 출력 루프
while True:
    values1 = read_adc(adc1)
    values2 = read_adc(adc2)
    values3 = read_adc(adc3)
    values4 = read_adc(adc4)

    # 결과 출력
    print("Module 1 Currents (mA): ", values1)
    print("Module 2 Currents (mA): ", values2)
    print("Module 3 Currents (mA): ", values3)
    print("Module 4 Currents (mA): ", values4)
    
    # 1초 대기
    time.sleep(1)
