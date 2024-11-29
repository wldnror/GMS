import RPi.GPIO as GPIO
import time

# GPIO 핀 번호 설정 (BCM 모드 사용)
COOLER_PIN = 17

# GPIO 설정
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(COOLER_PIN, GPIO.OUT)

def control_cooler(temp):
    """
    입력된 온도에 따라 쿨러를 켜거나 끕니다.
    
    Parameters:
        temp (float): 현재 온도
    """
    if temp >= 50.0:
        GPIO.output(COOLER_PIN, GPIO.HIGH)  # 쿨러 켜기
        print(f"온도가 {temp}°C 이상입니다. 쿨러를 켭니다.")
    else:
        GPIO.output(COOLER_PIN, GPIO.LOW)   # 쿨러 끄기
        print(f"온도가 {temp}°C 미만입니다. 쿨러를 끕니다.")

def cleanup():
    """GPIO 설정을 정리합니다."""
    GPIO.cleanup()
    print("GPIO 정리 완료.")

if __name__ == "__main__":
    try:
        while True:
            user_input = input("온도를 입력하세요 (종료하려면 'exit' 입력): ")
            if user_input.lower() == 'exit':
                print("프로그램을 종료합니다.")
                break
            try:
                temperature = float(user_input)
                control_cooler(temperature)
            except ValueError:
                print("유효한 숫자를 입력하세요.")
    except KeyboardInterrupt:
        print("\n사용자에 의해 중단되었습니다.")
    finally:
        cleanup()
