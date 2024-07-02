import numpy as np
import tflite_runtime.interpreter as tflite

# TensorFlow Lite 모델 로드
interpreter = tflite.Interpreter(model_path='gas_detection_model.tflite')
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

# 예측 예시
# test_data는 실제 측정된 센서 데이터로 대체되어야 합니다.
test_data = np.random.normal(loc=15000, scale=2000, size=(60,))  # 예시 데이터
prediction = predict_gas(test_data)
print(f"Predicted gas: {'IPA' if prediction == 0 else 'Ethanol'}")
