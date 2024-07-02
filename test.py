import numpy as np
from smbus2 import SMBus
import time

# I2C 버스 번호 및 주소
BUS_NUMBER = 1
DEVICE_ADDRESS = 0x54
bus = SMBus(BUS_NUMBER)

def read_sensor_data():
    try:
        bus.write_byte(DEVICE_ADDRESS, 0x52)
        time.sleep(0.1)
        data = bus.read_i2c_block_data(DEVICE_ADDRESS, 0x00, 7)
        if data[0] == 0x08:
            concentration = (data[1] << 8) | data[2]
            if 0 <= concentration <= 20000:
                return concentration
            else:
                return None
        else:
            return None
    except Exception as e:
        print(f"Error reading from sensor: {e}")
        return None

def collect_data(samples=100, time_steps=60):
    ipa_data = []
    ethanol_data = []
    for _ in range(samples):
        sample_data = []
        for _ in range(time_steps):
            data = read_sensor_data()
            if data is not None:
                sample_data.append(data)
            time.sleep(1)
        if len(sample_data) == time_steps:
            ipa_data.append(sample_data)
        ethanol_data.append(sample_data)
    return ipa_data, ethanol_data

# 데이터 수집
ipa_data, ethanol_data = collect_data()

# 라벨링
ipa_labels = [0] * len(ipa_data)
ethanol_labels = [1] * len(ethanol_data)

# 데이터 합치기
all_data = ipa_data + ethanol_data
all_labels = ipa_labels + ethanol_labels

# 경량화된 결정 트리 모델 구현
class SimpleDecisionTree:
    def fit(self, X, y):
        self.threshold = np.mean([np.mean(x) for x in X])
    
    def predict(self, X):
        return [0 if np.mean(x) < self.threshold else 1 for x in X]

# 모델 학습
model = SimpleDecisionTree()
model.fit(all_data, all_labels)

# 예측 수행
sensor_data = []
for _ in range(60):
    data = read_sensor_data()
    if data is not None:
        sensor_data.append(data)
    time.sleep(1)

if len(sensor_data) == 60:
    prediction = model.predict([sensor_data])
    print(f"Predicted gas: {'IPA' if prediction[0] == 0 else 'Ethanol'}")
else:
    print("Sensor data collection failed.")
