import csv
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import classification_report
import joblib

def load_data(filename):
    data = []
    labels = []
    with open(filename, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            labels.append(int(row[0]))
            data.append(list(map(int, row[1:])))
    return pd.DataFrame(data), labels

# 데이터 로드
ethanol_data, ethanol_labels = load_data('에탄올_data.csv')
ipa_data, ipa_labels = load_data('ipa_data.csv')

# 데이터 합치기
data = pd.concat([ethanol_data, ipa_data])
labels = ethanol_labels + ipa_labels

# 데이터 분할
X_train, X_test, y_train, y_test = train_test_split(data, labels, test_size=0.2, random_state=42)

# 모델 훈련
clf = DecisionTreeClassifier()
clf.fit(X_train, y_train)

# 모델 저장
joblib.dump(clf, 'gas_classifier.pkl')

# 예측
y_pred = clf.predict(X_test)

# 결과 출력
print(classification_report(y_test, y_pred))
