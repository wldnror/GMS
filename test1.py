#!/usr/bin/env python3
# auto_zero_cal.py - 슬레이브 ID 자동 탐색 후 Zero Calibration 수행

import sys
import time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# 탐색할 슬레이브 ID 범위
SLAVE_RANGE = range(1, 11)
# 테스트할 레지스터 (읽기용)
TEST_REG = 40001 - 1
# Zero Cal 레지스터
ZERO_CAL_REG = 40092 - 1

def find_slave(client):
    """1~10번 ID 중 읽기 응답이 오는 첫 번째 슬레이브 ID를 반환."""
    for sid in SLAVE_RANGE:
        try:
            resp = client.read_holding_registers(TEST_REG, 1, slave=sid)
            if not resp.isError():
                print(f"[Info] 응답 확인된 슬레이브 ID: {sid}")
                return sid
        except ModbusException:
            pass
    return None

def auto_zero_cal(ip, port=502, timeout=3):
    client = ModbusTcpClient(ip, port=port, timeout=timeout)
    if not client.connect():
        print(f"[Error] {ip}:{port} 연결 실패")
        return

    try:
        # 1) 슬레이브 ID 자동 탐색
        slave = find_slave(client)
        if slave is None:
            print("[Error] 유효한 슬레이브 ID를 찾을 수 없습니다 (1~10번).")
            return

        # 2) 읽기 테스트 (40001)
        resp = client.read_holding_registers(TEST_REG, 1, slave=slave)
        print(f"40001 값: {resp.registers[0]}")

        # 3) Zero Cal 쓰기 (40092 BIT0=1)
        wr = client.write_register(ZERO_CAL_REG, 1, slave=slave)
        if wr.isError():
            print(f"[Error] Zero Cal 쓰기 실패: {wr}")
            return
        print("Zero Cal 쓰기 성공")

        # 4) 센서 처리 시간을 위해 대기
        time.sleep(2)

        # 5) 쓰기 후 상태 읽기 (40092 BIT0)
        resp2 = client.read_holding_registers(ZERO_CAL_REG, 1, slave=slave)
        if resp2.isError():
            print(f"[Error] 40092 상태 읽기 실패: {resp2}")
        else:
            bit0 = resp2.registers[0] & 0x1
            state = "진행 중 (BIT0=1)" if bit0 else "완료 (BIT0=0)"
            print("Zero Cal 상태:", state)

    except ModbusException as e:
        print(f"[Exception] Modbus 오류: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 auto_zero_cal.py <SENSOR_IP>")
        sys.exit(1)

    ip = sys.argv[1]
    auto_zero_cal(ip)
