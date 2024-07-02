import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from sklearn.model_selection import train_test_split
from tensorflow.keras.utils import to_categorical
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

# 데이터 정규화
X_train = np.array(X_train) / 20000.0
X_test = np.array(X_test) / 20000.0

# 라벨을 원-핫 인코딩
y_train = to_categorical(y_train, 2)
y_test = to_categorical(y_test, 2)

# LSTM 모델 구축
model = Sequential([
    LSTM(50, activation='relu', input_shape=(X_train.shape[1], 1)),
    Dense(2, activation='softmax')
])

# 모델 컴파일
model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])

# 모델 학습
history = model.fit(X_train, y_train, epochs=10, validation_data=(X_test, y_test))

# 모델 평가
test_loss, test_acc = model.evaluate(X_test, y_test)
print(f"Test accuracy: {test_acc}")

# TensorFlow Lite 모델로 변환
converter = tf.lite.TFLiteConverter.from_keras_model(model)
tflite_model = converter.convert()

# 변환된 모델 저장
with open('gas_detection_model.tflite', 'wb') as f:
    f.write(tflite_model)
