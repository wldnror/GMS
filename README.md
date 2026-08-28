# GMS — Raspberry Pi 가스 모니터링 콘솔

GMS는 Raspberry Pi에 연결한 Modbus TCP 가스검지기, ADS1115 기반 4–20 mA 입력, INA219 UPS 정보를 하나의 Tkinter 화면에서 운용하는 현장용 콘솔입니다. 전체 패널은 최대 12개이며, 경보/고장 표시, CSV 로그, 장비 설정 및 TFTP 펌웨어 배포를 지원합니다.

이 문서는 **Raspberry Pi OS Desktop**을 전제로 합니다. Raspberry Pi OS Lite, SSH 전용 또는 디스플레이 서버가 없는 headless 환경에서는 Tk UI를 실행할 수 없습니다. 하드웨어 모듈을 연결하지 않은 개발 PC에서 순수 로직 테스트는 가능하지만, 그것이 GPIO/I²C/오디오의 현장 동작을 보증하지는 않습니다.

## 지원 범위

| 보드 | OS / 아키텍처 | Python | 상태 |
|---|---|---:|---|
| Raspberry Pi 3 / 3B+ | Raspberry Pi OS Bullseye Desktop 32-bit (`armv7l`) | 3.9 | 지원 |
| Raspberry Pi 4 | Raspberry Pi OS Bookworm Desktop 64-bit (`aarch64`) | 3.11 | 권장 |
| Pi 3 + Bookworm 또는 Pi 4 + Bullseye | Desktop 32/64-bit | OS 기본 Python | 최선 지원, 현장 재검증 필요 |
| Raspberry Pi OS Lite/headless | 모든 버전 | 모든 버전 | UI 운용 미지원 |

Python 3.13은 CI에서 문법과 순수 로직의 선행 호환성만 확인합니다. 현재 현장 배포 기준은 Python 3.9/3.11입니다. Pi 3에서는 1280×720, `GMS_UI_SCALE=1.0`을 권장하며 다른 데스크톱 프로그램을 최소화하십시오.

`requirements.txt`의 Pillow, psutil, cryptography, pygame 최소 버전은 Bullseye/Bookworm 패키지를 재사용할 수 있도록 설정되어 있습니다. PyModbus API 차이를 막기 위해 Python 3.9에는 `3.8.6`, Python 3.10 이상에는 `3.15.0`을 정확히 고정합니다. matplotlib와 rich는 런타임에서 사용하지 않으므로 의존성에서 제거했습니다.

## 1. OS와 Python 설치

먼저 OS 패키지를 사용해 Raspberry Pi용 네이티브 라이브러리를 설치합니다. Bookworm의 PEP 668 정책을 우회하려고 시스템 Python에 `--break-system-packages`를 사용하지 마십시오.

```bash
sudo apt update
sudo apt install -y \
  git fonts-nanum i2c-tools tftpd-hpa \
  python3-full python3-venv python3-tk \
  python3-pil python3-pil.imagetk python3-psutil python3-cryptography \
  python3-pygame python3-rpi.gpio python3-smbus
```

SDL/오디오 라이브러리는 `python3-pygame`이 OS에 맞는 버전을 함께 설치합니다. GMS는 PortAudio를 직접 사용하지 않습니다.

저장소와 venv를 준비합니다. 이 문서의 사용자 서비스 예제는 경로를 `~/GMS`로 가정합니다.

```bash
git clone https://github.com/wldnror/GMS.git "$HOME/GMS"
cd "$HOME/GMS"
python3 -m venv --system-site-packages myenv
myenv/bin/python -m pip install -r requirements.txt
myenv/bin/python -m pip check
```

`--system-site-packages`가 중요합니다. Raspberry Pi OS가 ARM에서 검증한 Pillow/cryptography/pygame 등의 바이너리를 재사용하므로 Pi 3에서 무거운 소스 빌드와 메모리 부족을 피할 수 있습니다. 보안 업데이트는 계속 `sudo apt update && sudo apt upgrade`로 적용하십시오.

## 2. I²C와 장치 그룹

`sudo raspi-config`의 **Interface Options → I2C**에서 I²C를 활성화한 뒤 재부팅합니다. Bullseye와 Bookworm은 부트 설정 파일 위치가 다르므로 직접 파일을 편집하는 것보다 `raspi-config`가 안전합니다.

현재 로그인 사용자를 필요한 장치 그룹에 추가합니다.

```bash
sudo usermod -aG gpio,i2c,audio,video "$USER"
if getent group render >/dev/null; then
  sudo usermod -aG render "$USER"
fi
```

그룹 변경은 **완전히 로그아웃했다가 다시 로그인하거나 재부팅한 뒤** 적용됩니다. 새 세션에서 확인합니다.

```bash
id
ls -l /dev/i2c-1
i2cdetect -l
```

사전점검은 버스만 열거하고 I²C 주소를 스캔하지 않습니다. `i2cdetect -y 1`은 일부 장치에 의도치 않은 명령을 보낼 수 있으므로 ADS1115/INA219 제조사 문서가 허용하고 배선을 확인한 경우에만 현장에서 수행하십시오.

## 3. TFTP 서버

펌웨어 업그레이드는 장비가 Raspberry Pi의 UDP/69 TFTP 서버에서 다음 파일을 읽는 방식입니다.

```text
/srv/tftp/GDS/ASGD-3200/asgd3200.bin
/srv/tftp/GDS/ASGD-3210/asgd3210.bin
```

GMS는 현재 장비 모델이 요청하는 경로를 모두 지원하기 위해, 사용자가 확인한 동일한 펌웨어를 위 두 경로에 SHA-256 검증 후 원자적으로 스테이징합니다. 선택한 파일이 해당 장비 하드웨어와 호환되는지는 반드시 제조사 배포 정보로 별도 확인하십시오.

`tftpd-hpa` 설치 후 서비스 계정과 그룹을 확인하고, GMS 사용자가 펌웨어를 원자적으로 스테이징할 수 있는 공유 디렉터리를 만듭니다.

```bash
getent passwd tftp
getent group tftp
sudo install -d -o tftp -g tftp -m 2770 /srv/tftp
sudo install -d -o tftp -g tftp -m 2770 /srv/tftp/GDS
sudo install -d -o tftp -g tftp -m 2770 /srv/tftp/GDS/ASGD-3200
sudo install -d -o tftp -g tftp -m 2770 /srv/tftp/GDS/ASGD-3210
sudo usermod -aG tftp "$USER"
```

`/etc/default/tftpd-hpa`를 다음 기준으로 검토합니다. 기존 운영 설정이 있다면 덮어쓰지 말고 병합하십시오.

```ini
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="/srv/tftp"
TFTP_ADDRESS=":69"
TFTP_OPTIONS="--secure"
```

적용 후 다시 로그인하고 서비스를 확인합니다.

```bash
sudo systemctl enable --now tftpd-hpa
sudo systemctl restart tftpd-hpa
systemctl status tftpd-hpa --no-pager
ss -lun | grep ':69'
namei -l /srv/tftp/GDS/ASGD-3200
namei -l /srv/tftp/GDS/ASGD-3210
```

UFW를 사용한다면 장비 전용 서브넷으로 UDP/69를 제한합니다. 아래 CIDR은 예시이므로 실제 장비망으로 바꾸십시오.

```bash
sudo ufw allow from 192.168.10.0/24 to any port 69 proto udp
```

TFTP는 최초 요청 뒤 동적 UDP 포트를 사용합니다. 방화벽/NAT가 상태 추적 응답을 허용하는지, 장비 VLAN에서 Raspberry Pi의 선택한 IPv4로 왕복 가능한지를 **다른 호스트 또는 실제 검출기에서** 시험해야 합니다. 로컬 `ss` 결과만으로 원격 접근성을 증명할 수 없습니다. 여러 NIC/Wi-Fi가 있으면 UI에 표시된 TFTP IP가 검출기까지 가는 인터페이스의 주소인지 반드시 확인하십시오.

## 4. 상태 디렉터리와 최초 실행

서비스와 수동 실행 모두 같은 영구 상태 경로를 사용하도록 먼저 만듭니다.

```bash
install -d -m 700 "$HOME/.local/state/gms"
export GMS_STATE_DIR="$HOME/.local/state/gms"
cd "$HOME/GMS"
./scripts/preflight.sh --tftp-ip 192.168.10.2
myenv/bin/python main.py
```

`192.168.10.2`는 Raspberry Pi의 실제 장비망 IPv4로 바꾸고, preflight는 로그인한 **그래픽 데스크톱의 터미널**에서 실행하십시오. SSH 세션에서는 DISPLAY/audio/user-session 검사가 실패하는 것이 정상입니다. 사전점검 스크립트는 읽기 전용입니다. 패키지 설치, 디렉터리 생성, 권한 변경, 서비스 시작, I²C 주소 스캔 또는 UDP 패킷 전송을 하지 않으며 문제를 발견하면 0이 아닌 상태로 끝납니다.

GMS는 다음과 같은 파일을 `GMS_STATE_DIR` 아래에 보관합니다.

- `secret.key`와 암호화된 `settings.json`
- `modbus_settings.json`
- 업데이트 무시/동기화 상태
- `analog_logs/`의 아날로그 CSV 로그

환경변수를 설정하지 않은 수동 실행은 호환성을 위해 저장소 디렉터리를 사용할 수 있으므로, 운영 장비에서는 항상 절대 경로의 `GMS_STATE_DIR`을 지정하십시오.

## 5. 화면 배율과 그래픽/오디오 세션

`GMS_UI_SCALE`은 절대 배율이며 안전 범위로 제한됩니다.

| 해상도 | 시작 권장값 |
|---|---:|
| 1280×720 | `1.0` |
| 1920×1080 | `1.65` (기본값) |

예를 들어 수동 실행은 다음과 같습니다.

```bash
GMS_STATE_DIR="$HOME/.local/state/gms" GMS_UI_SCALE=1.0 \
  "$HOME/GMS/myenv/bin/python" "$HOME/GMS/main.py"
```

Bookworm Desktop은 Wayland/labwc 구성이 일반적이지만 현재 Tk는 데스크톱 세션의 X11/XWayland `DISPLAY`가 필요할 수 있습니다. 사용자 로그인 세션의 `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, DBus와 PipeWire/PulseAudio 환경을 그대로 사용하는 것이 중요합니다. `DISPLAY=:0`을 무조건 지정하면 다른 좌석/세션을 잘못 열 수 있으므로 고정하지 마십시오.

## 6. Bookworm 사용자 systemd 서비스

시스템 서비스가 아니라 **로그인한 그래픽 사용자의 systemd 서비스**로 실행합니다. 이렇게 해야 화면, 오디오, DBus 세션과 장치 그룹이 일치합니다.

```bash
install -d -m 700 "$HOME/.config/systemd/user" "$HOME/.config/gms"
install -m 644 "$HOME/GMS/deploy/gms-session.service" \
  "$HOME/.config/systemd/user/gms.service"
```

저장소가 `~/GMS`가 아니라면 복사한 `gms.service`의 `WorkingDirectory`, `ExecStartPre`, `ExecStart` 경로를 실제 위치에 맞게 수정하십시오. 선택 배율은 `$HOME/.config/gms/gms.env`에 둘 수 있습니다.

```bash
test -e "$HOME/.config/gms/gms.env" || \
  printf '%s\n' 'GMS_UI_SCALE=1.65' >"$HOME/.config/gms/gms.env"
chmod 600 "$HOME/.config/gms/gms.env"
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
systemctl --user daemon-reload
systemctl --user enable --now gms.service
systemctl --user status gms.service --no-pager
journalctl --user -u gms.service -n 100 --no-pager
```

이 서비스는 `graphical-session.target`에 결합되어 사용자가 데스크톱에 로그인한 동안에만 실행됩니다. `loginctl enable-linger`로 GUI 프로그램을 로그인 전에 시작하지 마십시오. 로그아웃 시 종료되는 것이 의도된 동작입니다. 재시작 루프가 발생하면 먼저 `systemctl --user stop gms.service` 후 journal과 `scripts/preflight.sh` 결과를 확인하십시오.

종료 제한 시간은 90초로 두어 진행 중인 Git fetch/fast-forward 작업과 상태 저장이 SIGKILL 전에 정상적으로 정리될 여유를 줍니다.

네트워크나 검출기가 늦게 올라오는 것은 프로세스 재시작 사유가 아닙니다. Modbus 작업자는 중지 요청이 올 때까지 상한이 있는 지수 backoff로 재연결을 계속하며 UI에 상태를 표시합니다. 장시간 단절 후 복구는 반드시 현장 soak test에 포함하십시오.

## 7. 상태 백업과 복구

`secret.key`와 `settings.json`은 **같은 시점의 한 세트**입니다. 키만 잃어버리거나 다른 장비의 키를 섞거나 복호화된 설정 구조가 손상되면, GMS는 박스 0개의 기본값으로 조용히 시작하지 않고 복구 전까지 시작을 중단합니다. 손상본은 같은 내용당 하나의 `settings.broken-*.json` 사본으로 보존되며 원본도 그대로 남습니다.

백업할 때는 쓰기 중인 파일이 없도록 서비스를 멈춥니다.

```bash
systemctl --user stop gms.service
GMS_STATE_DIR="$HOME/.local/state/gms"
backup="$HOME/gms-state-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -C "$(dirname "$GMS_STATE_DIR")" -czf "$backup" "$(basename "$GMS_STATE_DIR")"
chmod 600 "$backup"
systemctl --user start gms.service
```

복구 전에는 현재 상태를 삭제하지 말고 옆으로 이동해 되돌릴 수 있게 보존합니다. 아래 `BACKUP_FILE`은 실제 백업 파일로 바꾸십시오.

```bash
systemctl --user stop gms.service
GMS_STATE_DIR="$HOME/.local/state/gms"
BACKUP_FILE="$HOME/gms-state-YYYYMMDD-HHMMSS.tar.gz"
mv "$GMS_STATE_DIR" "${GMS_STATE_DIR}.before-restore-$(date +%Y%m%d-%H%M%S)"
tar -C "$(dirname "$GMS_STATE_DIR")" -xzf "$BACKUP_FILE"
chmod 700 "$GMS_STATE_DIR"
systemctl --user start gms.service
journalctl --user -u gms.service -n 100 --no-pager
```

백업 파일은 설정 암호화 키를 포함하므로 장비 외부의 접근 통제된 저장소에도 보관하고 일반 Git/공유 폴더에는 올리지 마십시오.

## 8. 진단과 테스트

하드웨어를 건드리지 않는 자동 테스트:

```bash
cd "$HOME/GMS"
myenv/bin/python -m compileall -q .
myenv/bin/python -m unittest discover -s tests -v
myenv/bin/python -m pip check
```

CI는 Python 3.9/3.11/3.13에서 compile/unittest, Ruff `E/F/I`, Python 3.9/3.11에서 각각 고정한 Pillow/PyModbus로 production import와 암호화 상태 저장 테스트를 수행합니다. 로컬에서 같은 정적 검사를 하려면 다음을 사용합니다.

```bash
myenv/bin/python -m pip install ruff==0.16.5
myenv/bin/ruff check . --select E,F,I --ignore E501
```

루트의 `test.py`, `test1.py`, `test2.py`는 자동 단위 테스트가 아니라 현장 진단 도구입니다. 모두 import만으로 장치를 열지 않습니다.

```bash
# ADS1115 읽기 전용, 10회 샘플
myenv/bin/python test.py --samples 10 --interval 1

# Modbus 버전 읽기 전용
myenv/bin/python test1.py 192.168.10.20 read-version

# 다음 명령들은 장치 상태를 변경하므로 --yes가 반드시 필요
myenv/bin/python test1.py 192.168.10.20 set-tftp 192.168.10.2 --yes
myenv/bin/python test1.py 192.168.10.20 upgrade --timeout 300 --yes
myenv/bin/python test1.py 192.168.10.20 zero-cal --yes

# GPIO12 출력 변경. 배선/릴레이 논리를 확인한 뒤 실행
myenv/bin/python test2.py --temperature 49 --temperature 50 --yes
```

Modbus 쓰기는 중복 동작을 막기 위해 자동 재시도하지 않습니다. 펌웨어 상태 읽기는 재연결할 수 있지만 `--timeout`이 지나면 실패로 종료하며, 응답 레지스터 길이가 부족하면 성공으로 간주하지 않습니다.

## 9. 출고 전 현장 검증

각 OS 이미지와 실제 배선 조합에서 최소한 다음을 확인하십시오.

1. 재부팅 → 데스크톱 로그인 → 사용자 서비스 시작/종료 및 단일 인스턴스 동작
2. 1280×720과 1920×1080에서 버튼, 키패드, 7-segment, 대화상자가 화면 밖으로 나가지 않는지
3. Modbus 12대 장시간 단절/복구, 잘못된 응답 길이, 장치 미지원 레지스터 처리
4. 40007 오류/FUT 비트, AL1/AL2, 외부 GPIO 경보 출력과 실제 릴레이 논리
5. ADS1115 주소/채널 매핑, 4/12/20 mA 기준점과 선로 단선/과전류 표시
6. INA219 전압/배터리 잔량, 오디오 경보, 오디오 장치 재연결
7. 실제 장비망에서 TFTP 파일 읽기, 방화벽, 올바른 NIC/IP, 성공/실패/시간초과 복구
8. 서비스 중지 상태의 백업과 동일 장비/교체 SD 카드에서의 복구
9. 전원 차단 후 설정/키/로그의 무결성 및 디스크 여유 공간

## 남아 있는 운영 위험

- **Git 업데이트 공급망:** 내장 업데이트 기능은 원격 Git 커밋을 가져와 적용할 수 있지만 커밋/릴리스 서명, 승인된 키 또는 재현 가능한 빌드로 코드를 검증하지 않습니다. 운영망에서는 저장소 쓰기 권한과 브랜치를 통제하고, 별도 서명/검토/스테이징 및 롤백 절차를 마련하기 전까지 자동 배포 수단으로 신뢰하지 마십시오.
- **펌웨어 진위:** GMS는 선택한 파일이 복사 중 바뀌지 않았는지 SHA-256으로 확인하지만, 해당 해시를 제조사가 서명한 신뢰 목록과 대조하지 않습니다. 즉 전송 무결성 검사는 서명 검증이 아니며 악성/오배포 펌웨어를 막지 못합니다. 제조사 서명 또는 별도 승인 해시를 현장 절차에서 검증하십시오.
- **하드웨어 검증:** CI와 비-Pi 개발 환경은 GPIO 전압, I²C 버스 전기 특성, 릴레이 fail-safe, 오디오 장치 및 네트워크 방화벽을 검증할 수 없습니다. 출고 판정은 위 현장 체크리스트를 통과한 실제 Pi 3/4 장비에서만 내리십시오.
