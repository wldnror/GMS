# ModbusTCP 수신 기능을 이용한 데이터 시각화

## 개요
ModbusTCP를 통해 수신한 데이터를 화면에 깔끔하게 표시합니다.

## 가상환경 활성화
### 새로운 가상환경 만들기
```bash
python3 -m venv myenv
```
### 가상환경 연결
```bash
source myenv/bin/activate
```

## 필요 라이브러리
```bash
pip install pymodbus
pip install rich
pip install Pillow
pip install matplotlib
pip install mplcursors
pip install adafruit-ads1x15
pip install psutil
pip install cryptography

```

# 필수 설정

## systemd 서비스 파일 수정


```bash
sudo nano /etc/systemd/system/myscript.service
```

### 다음 내용을 서비스 파일에 추가 또는 수정합니다:
```bash
[Unit]
Description=My Python Script
After=network.target

[Service]
WorkingDirectory=/home/user/GMS
ExecStart=/home/user/GMS/myenv/bin/python3 /home/user/GMS/main.py
Restart=always
User=user
Environment="DISPLAY=:0"
Environment="PATH=/home/user/GMS/myenv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=multi-user.target
```

## 서비스 재시작 

### systemd 서비스를 다시 시작하여 변경 사항을 적용합니다.
```bash
sudo systemctl daemon-reload
sudo systemctl restart myscript.service
```


