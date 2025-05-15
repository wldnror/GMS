#!/usr/bin/env python3
# zero_cal_separate.py - 쓰기/읽기 세션 분리로 Zero Cal 수행

import sys
import time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# 테스트용 레지스터(읽기)
TEST_REG       = 40001 - 1
# 제로 캘리 레지스터(쓰기·읽기)
ZERO_CAL_REG   = 40092 - 1
# 슬레이브 ID (자동 탐색 없이 1로 고정)
SLAVE_ID       = 1
# 센서 리부팅·처리 예상 시간 (초)
RESET_DELAY    = 5

def write_zero_cal(ip, port=502):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] {ip}:{port} 연결 실패 (쓰기)")
        return False

    try:
        # Zero Cal 쓰기 (Function 0x06)
        wr = client.write_register(ZERO_CAL_REG, 1, slave=SLAVE_ID)
        # 센서 리부팅으로 응답이 없을 때는 예외가 날 수 있으니, 에러 시에도 진행
        if wr.isError():
            print(f"[Warn ] 쓰기 응답 실패: {wr} (무시하고 진행)")
        else:
            print("[Info ] Zero Cal 쓰기 성공, 센서 리부팅 중…")
        return True

    except ModbusException as e:
        print(f"[Warn ] 쓰기 중 예외 발생(무시): {e}")
        return True

    finally:
        client.close()

def read_zero_cal_status(ip, port=502):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] {ip}:{port} 연결 실패 (읽기)")
        return

    try:
        resp = client.read_holding_registers(ZERO_CAL_REG, 1, slave=SLAVE_ID)
        if resp.isError():
            print(f"[Error] 상태 읽기 실패: {resp}")
        else:
            bit0 = resp.registers[0] & 0x1
            state = "진행 중 (BIT0=1)" if bit0 else "완료 (BIT0=0)"
            print("[Info ] Zero Cal 상태:", state)
    finally:
        client.close()

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 zero_cal_separate.py <SENSOR_IP>")
        sys.exit(1)

    ip = sys.argv[1]

    # 1) 쓰기 세션: 명령 전송
    if not write_zero_cal(ip):
        sys.exit(1)

    # 2) 센서 리부팅/처리 대기
    print(f"[Info ] {RESET_DELAY}초 대기 후 상태 확인…")
    time.sleep(RESET_DELAY)

    # 3) 읽기 세션: 캘리 완료 여부 확인
    read_zero_cal_status(ip)

if __name__ == "__main__":
    main()
