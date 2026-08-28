#!/usr/bin/env bash
# Read-only Raspberry Pi deployment preflight.  This script never installs,
# enables, creates, chmods, probes an I2C address, or sends a UDP datagram.

set -u
set -o pipefail
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
STATE_DIR="${GMS_STATE_DIR:-${HOME}/.local/state/gms}"
TFTP_ROOT="/srv/tftp"
TFTP_IP=""
PYTHON_BIN=""

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS] %s\n' "$*"
}

warn() {
    WARN_COUNT=$((WARN_COUNT + 1))
    printf '[WARN] %s\n' "$*"
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[FAIL] %s\n' "$*"
}

usage() {
    cat <<'EOF'
Usage: scripts/preflight.sh [options]

Read-only checks for a GMS Raspberry Pi deployment.

  --app-dir PATH     repository path (default: script parent)
  --state-dir PATH   GMS_STATE_DIR (default: ~/.local/state/gms)
  --tftp-root PATH   TFTP root (default: /srv/tftp)
  --tftp-ip IPv4     address advertised to detector devices
  --python PATH      venv Python (default: APP_DIR/myenv/bin/python)
  -h, --help         show this help

No system setting or file is changed.  A UDP route check opens a local socket
but deliberately sends no packet.
EOF
}

while (($#)); do
    case "$1" in
        --app-dir)
            [[ $# -ge 2 ]] || { printf 'missing value: %s\n' "$1" >&2; exit 2; }
            APP_DIR="$2"
            shift 2
            ;;
        --state-dir)
            [[ $# -ge 2 ]] || { printf 'missing value: %s\n' "$1" >&2; exit 2; }
            STATE_DIR="$2"
            shift 2
            ;;
        --tftp-root)
            [[ $# -ge 2 ]] || { printf 'missing value: %s\n' "$1" >&2; exit 2; }
            TFTP_ROOT="$2"
            shift 2
            ;;
        --tftp-ip)
            [[ $# -ge 2 ]] || { printf 'missing value: %s\n' "$1" >&2; exit 2; }
            TFTP_IP="$2"
            shift 2
            ;;
        --python)
            [[ $# -ge 2 ]] || { printf 'missing value: %s\n' "$1" >&2; exit 2; }
            PYTHON_BIN="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="${APP_DIR}/myenv/bin/python"
fi

printf 'GMS preflight (read-only)\n'
printf '  app:    %s\n' "${APP_DIR}"
printf '  state:  %s\n' "${STATE_DIR}"
printf '  TFTP:   %s\n' "${TFTP_ROOT}"
printf '  Python: %s\n\n' "${PYTHON_BIN}"

OS_CODENAME=""
if [[ -r /etc/os-release ]]; then
    OS_NAME="$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n 1 | tr -d '"')"
    OS_CODENAME="$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release | head -n 1)"
    case "${OS_CODENAME}" in
        bullseye|bookworm) pass "OS: ${OS_NAME:-${OS_CODENAME}}" ;;
        *) fail "검증 대상은 Raspberry Pi OS Bullseye/Bookworm입니다: ${OS_NAME:-unknown}" ;;
    esac
else
    fail "/etc/os-release를 읽을 수 없습니다"
fi

ARCH="$(uname -m 2>/dev/null || true)"
case "${ARCH}" in
    armv7l) pass "아키텍처: armv7l (32-bit)" ;;
    aarch64) pass "아키텍처: aarch64 (64-bit)" ;;
    *) fail "Raspberry Pi 배포 아키텍처가 아닙니다: ${ARCH:-unknown}" ;;
esac

case "${OS_CODENAME}:${ARCH}" in
    bullseye:armv7l) pass "현장 지원 조합: Bullseye 32-bit / Pi 3 계열" ;;
    bookworm:aarch64) pass "권장 조합: Bookworm 64-bit / Pi 4 계열" ;;
    bullseye:*|bookworm:*) warn "OS/아키텍처 조합은 최선 지원 대상이며 현장 재검증이 필요합니다" ;;
esac

if [[ -r /proc/device-tree/model ]]; then
    PI_MODEL="$(tr -d '\000' </proc/device-tree/model)"
    case "${PI_MODEL}" in
        *"Raspberry Pi 3"*|*"Raspberry Pi 4"*) pass "보드: ${PI_MODEL}" ;;
        *) fail "현장 검증 대상(Pi 3/4)과 다른 보드: ${PI_MODEL}" ;;
    esac
else
    fail "Raspberry Pi 보드 모델을 확인할 수 없습니다"
fi

if [[ -f "${APP_DIR}/main.py" ]]; then
    pass "애플리케이션 파일: ${APP_DIR}/main.py"
else
    fail "main.py가 없습니다: ${APP_DIR}"
fi

if [[ -x "${PYTHON_BIN}" ]]; then
    PYTHON_VERSION="$(${PYTHON_BIN} -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
    case "${PYTHON_VERSION}" in
        3.9.*|3.11.*) pass "지원 Python: ${PYTHON_VERSION}" ;;
        *) fail "지원 Python은 3.9 또는 3.11입니다: ${PYTHON_VERSION:-unknown}" ;;
    esac
    case "${OS_CODENAME}:${PYTHON_VERSION}" in
        bullseye:3.9.*|bookworm:3.11.*) pass "OS 기본 Python 계열과 일치" ;;
        bullseye:*|bookworm:*) warn "OS 기본 Python(Bullseye 3.9 / Bookworm 3.11)과 다릅니다" ;;
    esac
else
    fail "실행 가능한 venv Python이 없습니다: ${PYTHON_BIN}"
fi

if [[ -f "${APP_DIR}/myenv/pyvenv.cfg" ]]; then
    if grep -Eqi '^include-system-site-packages[[:space:]]*=[[:space:]]*true' "${APP_DIR}/myenv/pyvenv.cfg"; then
        pass "venv가 Raspberry Pi OS 시스템 패키지를 재사용합니다"
    else
        warn "venv가 --system-site-packages로 만들어지지 않았습니다"
    fi
else
    warn "${APP_DIR}/myenv/pyvenv.cfg를 확인할 수 없습니다"
fi

if [[ -x "${PYTHON_BIN}" ]]; then
    for package in Pillow psutil cryptography pymodbus; do
        if "${PYTHON_BIN}" - "${package}" >/dev/null 2>&1 <<'PY'
import importlib.metadata
import sys

print(importlib.metadata.version(sys.argv[1]))
PY
        then
            VERSION="$(${PYTHON_BIN} - "${package}" <<'PY'
import importlib.metadata
import sys

print(importlib.metadata.version(sys.argv[1]))
PY
)"
            pass "Python 패키지 ${package} ${VERSION}"
        else
            fail "필수 Python 패키지 누락: ${package}"
        fi
    done

    if "${PYTHON_BIN}" -c 'import tkinter' >/dev/null 2>&1; then
        pass "Tkinter 사용 가능"
    else
        fail "Tkinter 누락: python3-tk 설치를 확인하세요"
    fi

    for package in RPi.GPIO Adafruit-ADS1x15 adafruit-blinka adafruit-circuitpython-ina219 pygame; do
        if "${PYTHON_BIN}" - "${package}" >/dev/null 2>&1 <<'PY'
import importlib.metadata
import sys

importlib.metadata.version(sys.argv[1])
PY
        then
            pass "하드웨어 패키지 메타데이터: ${package}"
        else
            warn "하드웨어 경로에서 필요할 수 있는 패키지 누락: ${package}"
        fi
    done
fi

if [[ -d "${STATE_DIR}" ]]; then
    if [[ -r "${STATE_DIR}" && -w "${STATE_DIR}" && -x "${STATE_DIR}" ]]; then
        pass "상태 디렉터리 접근 가능: ${STATE_DIR}"
    else
        fail "상태 디렉터리에 읽기/쓰기/탐색 권한이 필요합니다: ${STATE_DIR}"
    fi
    if command -v stat >/dev/null 2>&1; then
        STATE_MODE="$(stat -c '%a' "${STATE_DIR}" 2>/dev/null || true)"
        case "${STATE_MODE}" in
            700|0700) pass "상태 디렉터리 권한: ${STATE_MODE}" ;;
            *) warn "상태 디렉터리는 mode 700 권장: ${STATE_MODE:-unknown}" ;;
        esac
    fi

    if [[ -e "${STATE_DIR}/settings.json" && ! -e "${STATE_DIR}/secret.key" ]]; then
        fail "settings.json은 있지만 secret.key가 없음(같은 시점 백업 복원 필요)"
    elif [[ -e "${STATE_DIR}/secret.key" && ! -e "${STATE_DIR}/settings.json" ]]; then
        warn "secret.key는 있지만 settings.json이 없음(최초 저장 전이면 정상)"
    elif [[ -e "${STATE_DIR}/secret.key" && -e "${STATE_DIR}/settings.json" ]]; then
        pass "secret.key/settings.json 백업 쌍 존재"
    else
        warn "아직 암호화 상태 파일이 없음(최초 실행 전이면 정상)"
    fi

    for state_file in secret.key settings.json modbus_settings.json; do
        STATE_PATH="${STATE_DIR}/${state_file}"
        [[ -e "${STATE_PATH}" ]] || continue
        if [[ -L "${STATE_PATH}" ]]; then
            fail "상태 파일은 심볼릭 링크이면 안 됨: ${STATE_PATH}"
            continue
        fi
        STATE_FILE_MODE="$(stat -c '%a' "${STATE_PATH}" 2>/dev/null || true)"
        case "${STATE_FILE_MODE}" in
            600|0600) pass "상태 파일 권한 ${state_file}: ${STATE_FILE_MODE}" ;;
            *) fail "상태 파일 ${state_file}는 mode 600이어야 함: ${STATE_FILE_MODE:-unknown}" ;;
        esac
    done
else
    STATE_PARENT="${STATE_DIR}"
    while [[ ! -e "${STATE_PARENT}" && "${STATE_PARENT}" != "/" ]]; do
        STATE_PARENT="$(dirname -- "${STATE_PARENT}")"
    done
    if [[ -d "${STATE_PARENT}" && -w "${STATE_PARENT}" && -x "${STATE_PARENT}" ]]; then
        fail "상태 디렉터리가 아직 없습니다(자동 생성하지 않음): ${STATE_DIR}"
    else
        fail "상태 디렉터리를 만들 수 있는 상위 경로 권한이 없습니다: ${STATE_PARENT}"
    fi
fi

if df -Pk "${APP_DIR}" >/dev/null 2>&1; then
    AVAILABLE_KB="$(df -Pk "${APP_DIR}" | awk 'NR==2 {print $4}')"
    if [[ "${AVAILABLE_KB}" =~ ^[0-9]+$ ]] && ((AVAILABLE_KB >= 524288)); then
        pass "가용 디스크: $((AVAILABLE_KB / 1024)) MiB"
    else
        warn "가용 디스크 512 MiB 이상 권장: $((AVAILABLE_KB / 1024)) MiB"
    fi
else
    warn "가용 디스크를 확인할 수 없습니다"
fi

if [[ -e /dev/i2c-1 ]]; then
    if [[ -r /dev/i2c-1 && -w /dev/i2c-1 ]]; then
        pass "/dev/i2c-1 읽기/쓰기 가능"
    else
        fail "/dev/i2c-1 권한이 없습니다(i2c 그룹 및 재로그인 확인)"
    fi
else
    fail "/dev/i2c-1이 없습니다(I2C 활성화 확인)"
fi

if command -v i2cdetect >/dev/null 2>&1; then
    if i2cdetect -l 2>/dev/null | grep -q 'i2c-1'; then
        pass "i2c-tools가 버스 1을 열거합니다(주소 스캔은 수행하지 않음)"
    else
        warn "i2cdetect -l에서 i2c-1을 찾지 못했습니다"
    fi
else
    fail "i2c-tools가 없습니다"
fi

GROUPS_NOW="$(id -nG 2>/dev/null || true)"
for group in gpio i2c; do
    if tr ' ' '\n' <<<"${GROUPS_NOW}" | grep -qx "${group}"; then
        pass "현재 세션이 ${group} 그룹에 속함"
    else
        fail "현재 세션이 ${group} 그룹에 속하지 않음(추가 후 재로그인 필요)"
    fi
done
for group in audio video render; do
    if getent group "${group}" >/dev/null 2>&1; then
        if tr ' ' '\n' <<<"${GROUPS_NOW}" | grep -qx "${group}"; then
            pass "현재 세션이 ${group} 그룹에 속함"
        else
            warn "${group} 그룹이 있지만 현재 세션에는 적용되지 않음"
        fi
    else
        warn "이 OS에는 ${group} 그룹이 없음(이미지별로 정상일 수 있음)"
    fi
done

SESSION_TYPE="${XDG_SESSION_TYPE:-}"
case "${SESSION_TYPE}" in
    x11|wayland) pass "그래픽 세션: ${SESSION_TYPE}" ;;
    *) fail "XDG_SESSION_TYPE이 x11/wayland가 아닙니다: ${SESSION_TYPE:-unset}" ;;
esac

if [[ -n "${DISPLAY:-}" ]]; then
    pass "Tk/X11 디스플레이 환경: DISPLAY=${DISPLAY}, WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset}"
elif [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
    fail "Wayland 세션은 있으나 Tk가 사용할 DISPLAY/XWayland가 없습니다: ${WAYLAND_DISPLAY}"
else
    fail "DISPLAY와 WAYLAND_DISPLAY가 모두 없습니다(headless에서는 UI 실행 불가)"
fi

if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" ]]; then
    pass "XDG_RUNTIME_DIR 접근 가능: ${XDG_RUNTIME_DIR}"
else
    fail "XDG_RUNTIME_DIR이 없거나 접근할 수 없습니다"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    USER_MANAGER_ENV="$(systemctl --user show-environment 2>/dev/null)"
    if grep -q '^DISPLAY=' <<<"${USER_MANAGER_ENV}"; then
        pass "systemd 사용자 관리자에 DISPLAY가 전달됨"
    else
        fail "systemd 사용자 관리자에 DISPLAY가 없음(import-environment 필요)"
    fi
    if systemctl --user is-active --quiet graphical-session.target 2>/dev/null; then
        pass "graphical-session.target 활성"
    else
        warn "graphical-session.target이 현재 활성 상태가 아닙니다"
    fi
    if systemctl --user is-enabled --quiet gms.service 2>/dev/null; then
        pass "gms.service 사용자 서비스 활성화됨"
    else
        warn "gms.service가 아직 enable되지 않았습니다"
    fi
else
    fail "systemd 사용자 관리자 환경을 조회할 수 없습니다"
fi

if command -v wpctl >/dev/null 2>&1 && wpctl status >/dev/null 2>&1; then
    pass "PipeWire 오디오 세션 응답"
elif command -v pactl >/dev/null 2>&1 && pactl info >/dev/null 2>&1; then
    pass "PulseAudio/PipeWire 오디오 세션 응답"
elif [[ -n "${XDG_RUNTIME_DIR:-}" && ( -S "${XDG_RUNTIME_DIR}/pipewire-0" || -S "${XDG_RUNTIME_DIR}/pulse/native" ) ]]; then
    pass "사용자 오디오 소켓 존재"
else
    warn "사용자 오디오 세션 응답을 확인하지 못했습니다"
fi

TFTP_DEVICE_DIRS=(
    "${TFTP_ROOT}/GDS/ASGD-3200"
    "${TFTP_ROOT}/GDS/ASGD-3210"
)
if [[ -r /etc/default/tftpd-hpa ]]; then
    if grep -Eq "^[[:space:]]*TFTP_DIRECTORY=[\"']?${TFTP_ROOT}[\"']?[[:space:]]*$" /etc/default/tftpd-hpa; then
        pass "tftpd-hpa root 설정이 일치함: ${TFTP_ROOT}"
    else
        fail "/etc/default/tftpd-hpa의 TFTP_DIRECTORY가 ${TFTP_ROOT}와 다릅니다"
    fi
    if grep -Eq "^[[:space:]]*TFTP_USERNAME=[\"']?tftp[\"']?[[:space:]]*$" /etc/default/tftpd-hpa; then
        pass "tftpd-hpa 서비스 계정: tftp"
    else
        warn "tftpd-hpa 서비스 계정과 디렉터리 읽기 권한을 수동 확인하세요"
    fi
    if grep -Eq '^[[:space:]]*TFTP_OPTIONS=.*--secure' /etc/default/tftpd-hpa; then
        pass "tftpd-hpa --secure 옵션 설정"
    else
        fail "tftpd-hpa TFTP_OPTIONS에 --secure가 필요합니다"
    fi
else
    fail "/etc/default/tftpd-hpa를 읽을 수 없습니다"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet tftpd-hpa.service 2>/dev/null; then
    pass "tftpd-hpa.service 활성"
else
    fail "tftpd-hpa.service가 활성 상태가 아닙니다"
fi

if getent group tftp >/dev/null 2>&1; then
    if tr ' ' '\n' <<<"${GROUPS_NOW}" | grep -qx tftp; then
        pass "현재 세션이 tftp 그룹에 속함"
    else
        fail "현재 세션이 tftp 그룹에 속하지 않음(추가 후 재로그인 필요)"
    fi
else
    fail "tftpd-hpa용 tftp 그룹을 찾을 수 없습니다"
fi

for TFTP_DEVICE_DIR in "${TFTP_DEVICE_DIRS[@]}"; do
    if [[ -L "${TFTP_DEVICE_DIR}" ]]; then
        fail "TFTP 장치 경로는 심볼릭 링크이면 안 됩니다: ${TFTP_DEVICE_DIR}"
    elif [[ -d "${TFTP_DEVICE_DIR}" ]]; then
        if [[ -w "${TFTP_DEVICE_DIR}" && -x "${TFTP_DEVICE_DIR}" ]]; then
            pass "GMS가 펌웨어를 스테이징할 수 있음: ${TFTP_DEVICE_DIR}"
        else
            fail "GMS 사용자에게 TFTP 장치 디렉터리 쓰기/탐색 권한 필요: ${TFTP_DEVICE_DIR}"
        fi
        if [[ -r "${TFTP_DEVICE_DIR}" && -x "${TFTP_DEVICE_DIR}" ]]; then
            pass "현재 사용자에게 TFTP 장치 디렉터리 읽기/탐색 권한 있음"
        else
            warn "TFTP 서비스 계정의 읽기/탐색 권한을 별도로 확인하세요"
        fi
    else
        fail "필수 TFTP 경로가 없습니다(자동 생성하지 않음): ${TFTP_DEVICE_DIR}"
    fi
done

if command -v ss >/dev/null 2>&1; then
    if ss -H -lun 2>/dev/null | awk '{address=$4; sub(/^.*:/, "", address); if (address == "69") found=1} END {exit !found}'; then
        pass "로컬 UDP/69 리스너 발견"
    else
        fail "로컬 UDP/69 리스너를 찾지 못했습니다"
    fi
else
    warn "ss 명령이 없어 UDP/69 리스너를 확인하지 못했습니다"
fi

if [[ -n "${TFTP_IP}" && -x "${PYTHON_BIN}" ]]; then
    if "${PYTHON_BIN}" - "${TFTP_IP}" >/dev/null 2>&1 <<'PY'
import ipaddress
import socket
import sys

address = ipaddress.ip_address(sys.argv[1])
if (
    address.version != 4
    or address.is_loopback
    or address.is_unspecified
    or address.is_multicast
    or address == ipaddress.ip_address("255.255.255.255")
):
    raise SystemExit(1)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    # connect() chooses a route for UDP; no datagram is sent here.
    sock.connect((str(address), 69))
    if sock.getsockname()[0] in {"0.0.0.0", "127.0.0.1"}:
        raise SystemExit(1)
finally:
    sock.close()
PY
    then
        pass "TFTP 광고 주소로 로컬 UDP 경로 선택 가능: ${TFTP_IP}"
    else
        fail "TFTP 광고 주소가 유효한 비루프백 IPv4이거나 로컬 경로가 아님: ${TFTP_IP}"
    fi

    if command -v ip >/dev/null 2>&1 && ip -o -4 address show 2>/dev/null | awk -v expected="${TFTP_IP}" '{split($4, address, "/"); if (address[1] == expected) found=1} END {exit !found}'; then
        pass "TFTP 광고 주소가 로컬 인터페이스에 할당됨: ${TFTP_IP}"
    else
        fail "TFTP 광고 주소가 로컬 인터페이스에 할당되지 않음: ${TFTP_IP}"
    fi
else
    warn "--tftp-ip를 지정해야 장치에 광고할 주소/경로를 확인할 수 있습니다"
fi

warn "원격 장치에서 UDP/69 및 TFTP 동적 응답 트래픽이 통과하는지는 현장 방화벽 시험이 필요합니다"

printf '\nSummary: PASS=%d WARN=%d FAIL=%d\n' "${PASS_COUNT}" "${WARN_COUNT}" "${FAIL_COUNT}"
if ((FAIL_COUNT > 0)); then
    exit 1
fi
exit 0
