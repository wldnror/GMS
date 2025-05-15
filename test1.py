#!/usr/bin/env python3
import time, sys
from pymodbus.client import ModbusTcpClient

def test_zero_cal(ip, port=502, slave=1):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] {ip}:{port} 연결 실패")
        return

    try:
        # --- 1) 읽기 테스트 (정상 동작 확인)
        resp = client.read_holding_registers(40001-1, 1, slave=slave)
        if resp.isError():
            print(f"[Error] 40001 읽기 실패: {resp}")
        else:
            print("40001 값:", resp.registers[0])

        # --- 2) Zero Cal 쓰기
        reg = 40092 - 1
        wr = client.write_register(reg, 1, slave=slave)
        print("Zero Cal 쓰기 결과:", wr)

        # 장비가 처리할 시간을 주기 위해 잠시 대기
        time.sleep(2)

        # --- 3) 쓰기 후 상태 읽기
        resp2 = client.read_holding_registers(reg, 1, slave=slave)
        if resp2.isError():
            print(f"[Error] 40092 상태 읽기 실패: {resp2}")
        else:
            bit0 = resp2.registers[0] & 0x1
            print("Zero Cal 상태 BIT0:", bit0, "(0=완료, 1=진행중)")

    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 test1.py <SENSOR_IP>")
    else:
        test_zero_cal(sys.argv[1])
