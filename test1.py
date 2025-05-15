#!/usr/bin/env python3
from pymodbus.client import ModbusTcpClient
import time, sys

def test_zero_cal(ip, port=502, slave_id=1):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] 연결 실패: {ip}:{port}")
        return
    try:
        reg = 40092 - 1
        val = 1

        # 쓰기: slave 인자 사용
        result = client.write_register(reg, val, slave=slave_id)
        print(f"[Debug] write_register 결과: {result}")

        time.sleep(1)

        # 읽기: 마찬가지로 slave 인자 사용
        resp = client.read_holding_registers(reg, 1, slave=slave_id)
        if resp.isError():
            print(f"[Error] 상태 읽기 실패: {resp}")
        else:
            status = resp.registers[0] & 0x1
            print("[Info] 캘리 중…" if status else "[Info] 캘리 완료")
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 test1.py <SENSOR_IP> [SLAVE_ID]")
        sys.exit(1)
    ip = sys.argv[1]
    slave = int(sys.argv[2]) if len(sys.argv) >= 3 else 1
    test_zero_cal(ip, slave_id=slave)
