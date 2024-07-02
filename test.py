import numpy as np
import matplotlib.pyplot as plt
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from sklearn.model_selection import train_test_split
from tensorflow.keras.utils import to_categorical

# 예시 데이터 생성 (실제 데이터로 대체해야 함)
def generate_example_data():
    time_steps = 60  # 60초 간격
    ipa_data = np.random.normal(loc=15000, scale=2000, size=(100, time_steps))
    ethanol_data = np.random.normal(loc=17000, scale=2000, size=(100, time_steps))
    ipa_labels = [0] * 100
    ethanol_labels = [1] * 100

    all_data = np.concatenate((ipa_data, ethanol_data), axis=0)
    all_labels = np.array(ipa_labels + ethanol_labels)

    return all_data, all_labels

# 데이터 로드 및 전처리 (실제 데이터로 대체)
all_data, all_labels = generate_example_data()

# 데이터 셔플 및 분할
X_train, X_test, y_train, y_test = train_test_split(all_data, all_labels, test_size=0.2, random_state=42)

# 데이터 정규화
X_train = X_train / 20000.0
X_test = X_test / 20000.0

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

# 결과 그래프 그리기
plt.plot(history.history['accuracy'], label='accuracy')
plt.plot(history.history['val_accuracy'], label = 'val_accuracy')
plt.xlabel('Epoch')
plt.ylabel('Accuracy')
plt.ylim([0, 1])
plt.legend(loc='lower right')
plt.show()

# 모델 저장
model.save('gas_detection_model.h5')
