import numpy as np
from smbus2 import SMBus
import time
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from micromlgen import port
import joblib

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
        # For simplicity, we use the same data for ethanol; replace with actual ethanol data collection
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

# 데이터 셔플 및 분할
X_train, X_test, y_train, y_test = train_test_split(all_data, all_labels, test_size=0.2, random_state=42)

# DecisionTreeClassifier 모델 구축
model = DecisionTreeClassifier()

# 모델 학습
model.fit(X_train, y_train)

# 모델 평가
y_pred = model.predict(X_test)
accuracy = (y_test == y_pred).sum() / len(y_test)
print(f"Test accuracy: {accuracy}")

# 모델 저장
joblib.dump(model, 'gas_detection_model.pkl')

# 모델 포팅 코드 생성
c_code = port(model)
with open('model.h', 'w') as f:
    f.write(c_code)
