# GMS — Modbus TCP / 4–20 mA 통합 모니터링

라즈베리파이에서 Modbus TCP 가스검지기, ADS1115 기반 4–20 mA 입력, INA219 UPS 정보를 한 화면에 표시하는 Tkinter 애플리케이션입니다.

## 주요 기능

- Modbus TCP 장비 최대 12대 구성 및 자동 재연결
- 40001~40024 데이터 표시, 경보·FUT·펌웨어 상태 확인
- 장비별 FW 업그레이드, ZERO, 재부팅, 모델 변경
- ADS1115 최대 3개(12채널)의 4–20 mA 입력 표시
- INA219 기반 UPS 전압 및 배터리 잔량 표시
- 장비별 메모리 로그, CSV 저장 및 그래프
- 암호화된 설정 저장과 Git 기반 프로그램 업데이트

## 설치

Raspberry Pi OS에서 다음 시스템 패키지를 설치합니다.

```bash
sudo apt update
sudo apt install -y \
  python3-full python3-venv python3-tk \
  fonts-nanum git i2c-tools libportaudio2
```

I2C를 활성화한 뒤 가상환경을 만들고 의존성을 설치합니다.

```bash
cd /home/user/GMS
python3 -m venv myenv
source myenv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

PyModbus는 3.8 이상에서 `count`, `values` 등의 인자를 키워드로 요구합니다. 현재 코드는 PyModbus 3.8~3.x 형식에 맞춰져 있으며 `requirements.txt`에서 4.0 미만으로 제한합니다.

## 실행

```bash
cd /home/user/GMS
source myenv/bin/activate
python main.py
```

GPIO, ADS1115, INA219 또는 오디오 장치가 없는 개발 환경에서도 UI 자체는 시작할 수 있도록 처리되어 있습니다. 해당 하드웨어 기능만 비활성화됩니다.

## systemd 자동 실행

`/etc/systemd/system/gms.service`를 생성합니다.

```ini
[Unit]
Description=GDSENG GMS Monitoring
After=network-online.target graphical.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/user/GMS
ExecStart=/home/user/GMS/myenv/bin/python /home/user/GMS/main.py
Restart=on-failure
RestartSec=3
User=user
Environment=DISPLAY=:0
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=graphical.target
```

적용합니다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gms.service
sudo systemctl status gms.service
```

## 로컬에서 생성되는 파일

다음 파일은 장비별 로컬 설정 또는 비밀정보이므로 Git에 올리지 않습니다.

- `secret.key`
- `settings.json`
- `modbus_settings.json`
- `ignore_commit.txt`
- `analog_logs/`

기존 장비에서 `secret.key`를 삭제하면 암호화된 `settings.json`을 더 이상 복호화할 수 없습니다. 두 파일은 함께 백업해야 합니다.

## 테스트

하드웨어 없이 실행 가능한 순수 로직 테스트와 문법 검사를 제공합니다.

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

실제 배포 전에는 반드시 현장 장비에서 다음을 확인하십시오.

1. Modbus 40007 오류/FUT 비트
2. AL1·AL2 및 외부 GPIO 경보 출력
3. 4–20 mA 채널 번호와 ADS1115 주소 매핑
4. HMDS 표시값·로그값·경보 설정값
5. FW 업그레이드용 `/srv/tftp/GDS/ASGD-3200/asgd3200.bin` 쓰기 권한
