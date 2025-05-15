#!/usr/bin/env python3
from pymodbus.client import ModbusTcpClient
import time, sys

def test_zero_cal(ip, port=502, unit_id=1):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] 연결 실패: {ip}:{port}")
        return
    try:
        reg = 40092 - 1   # Zero Cal 레지스터 (0-based)
        val = 1          # BIT0 = 1
        
        # 쓰기
        result = client.write_register(reg, val, unit=unit_id)
        print(f"[Debug] write_register 결과: {result}")
        
        # 1초 후 상태 읽기
        time.sleep(1)
        resp = client.read_holding_registers(reg, 1, unit=unit_id)
        if resp.isError():
            print(f"[Error] 상태 읽기 실패: {resp}")
        else:
            status = resp.registers[0] & 0x1
            if status:
                print("[Info] 아직 캘리 진행 중 (BIT0=1)")
            else:
                print("[Info] 캘리 완료 (BIT0=0)")
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 test1.py <SENSOR_IP> [UNIT_ID]")
        sys.exit(1)
    ip = sys.argv[1]
    unit = int(sys.argv[2]) if len(sys.argv) >= 3 else 1
    test_zero_cal(ip, unit_id=unit)
