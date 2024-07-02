import numpy as np
from tflite_runtime.interpreter import Interpreter
from smbus2 import SMBus
import time

# I2C 버스 번호 및 주소
BUS_NUMBER = 1
DEVICE_ADDRESS = 0x54
bus = SMBus(BUS_NUMBER)

# TensorFlow Lite 모델 로드
interpreter = Interpreter(model_path='gas_detection_model.tflite')
interpreter.allocate_tensors()

# 입력 및 출력 텐서 정보 가져오기
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

def preprocess_data(data):
    data = np.array(data).astype(np.float32) / 20000.0
    data = np.expand_dims(data, axis=0)
    data = np.expand_dims(data, axis=-1)
    return data

def predict_gas(data):
    data = preprocess_data(data)
    interpreter.set_tensor(input_details[0]['index'], data)
    interpreter.invoke()
    output_data = interpreter.get_tensor(output_details[0]['index'])
    return np.argmax(output_data)

def read_sensor_data():
    try:
        # 명령어를 보내 데이터 읽기 준비
        bus.write_byte(DEVICE_ADDRESS, 0x52)  # ASCII 'R'
        time.sleep(0.1)  # 데이터 준비 시간
        
        # 7 바이트 데이터 읽기
        data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 7)
        print(f"Raw data: {data}")

        if data[0] == 0x08:
            c4h10_concentration = (data[1] << 8) | data[2]
            if 0 <= c4h10_concentration <= 20000:  # 0~20000ppm 범위 내 값만 수용
                return c4h10_concentration
            else:
                print(f"Error: Abnormally high concentration value: {c4h10_concentration}")
                return None
        else:
            print("Error: Invalid header byte")
            return None
    except Exception as e:
        print(f"Error reading from sensor: {e}")
        return None

# 데이터 수집 및 예측
sensor_data = []
for _ in range(60):  # 60초 동안 데이터 수집
    data = read_sensor_data()
    if data is not None:
        sensor_data.append(data)
    time.sleep(1)

if len(sensor_data) == 60:
    prediction = predict_gas(sensor_data)
    print(f"Predicted gas: {'IPA' if prediction == 0 else 'Ethanol'}")
else:
    print("Sensor data collection failed.")
