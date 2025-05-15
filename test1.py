#!/usr/bin/env python3
from pymodbus.client import ModbusTcpClient
import time, sys

def test_zero_cal(ip, port=502, slave=1):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] 연결 실패: {ip}:{port}")
        return

    try:
        reg = 40092 - 1
        val = 1

        # slave 인자로 슬레이브 ID 지정
        result = client.write_register(reg, val, slave=slave)
        print(f"[Debug] write_register 결과: {result}")

        time.sleep(1)
        resp = client.read_holding_registers(reg, 1, slave=slave)
        if resp.isError():
            print(f"[Error] 상태 읽기 실패: {resp}")
        else:
            status = resp.registers[0] & 0x1
            print(f"[Info] 캘리 {'진행 중 (BIT0=1)' if status else '완료 (BIT0=0)'}")

    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 test1.py <SENSOR_IP>")
    else:
        test_zero_cal(sys.argv[1])
