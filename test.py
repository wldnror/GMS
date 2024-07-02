import numpy as np
import matplotlib.pyplot as plt
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Conv2D, MaxPooling2D, Flatten, Dense
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from sklearn.model_selection import train_test_split
from tensorflow.keras.utils import to_categorical
import cv2

# 데이터 전처리 함수
def preprocess_data(data):
    data = np.array(data)
    data = data / 255.0  # 정규화
    data = np.expand_dims(data, axis=-1)  # 채널 추가
    return data

# 데이터 로드 및 전처리 (예시 데이터 사용)
# 각 이미지의 크기를 28x28로 맞춤
ipa_images = [cv2.resize(img, (28, 28)) for img in ipa_data]
ethanol_images = [cv2.resize(img, (28, 28)) for img in ethanol_data]

# 라벨링
ipa_labels = [0] * len(ipa_images)
ethanol_labels = [1] * len(ethanol_images)

# 데이터 합치기
all_images = ipa_images + ethanol_images
all_labels = ipa_labels + ethanol_labels

# 데이터 셔플 및 분할
X_train, X_test, y_train, y_test = train_test_split(all_images, all_labels, test_size=0.2, random_state=42)

# 데이터 전처리
X_train = preprocess_data(X_train)
X_test = preprocess_data(X_test)
y_train = to_categorical(y_train, 2)
y_test = to_categorical(y_test, 2)

# CNN 모델 구축
model = Sequential([
    Conv2D(32, (3, 3), activation='relu', input_shape=(28, 28, 1)),
    MaxPooling2D((2, 2)),
    Conv2D(64, (3, 3), activation='relu'),
    MaxPooling2D((2, 2)),
    Flatten(),
    Dense(128, activation='relu'),
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
