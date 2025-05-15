#!/usr/bin/env python3
from pymodbus.client import ModbusTcpClient
import time
import sys

def test_zero_cal(ip, port=502):
    client = ModbusTcpClient(ip, port=port, timeout=3)
    if not client.connect():
        print(f"[Error] 연결 실패: {ip}:{port}")
        return

    try:
        # Zero Calibration: 레지스터 40092 (0-based 주소는 40092-1), BIT0에 1 쓰기
        reg = 40092 - 1
        val = 1
        result = client.write_register(reg, val)
        print(f"[Debug] write_register 결과: {result}")

        # 1초 대기 후 상태 읽기
        time.sleep(1)
        resp = client.read_holding_registers(reg, 1)
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
    if len(sys.argv) != 2:
        print("사용법: python3 test_zero_cal.py <SENSOR_IP>")
    else:
        test_zero_cal(sys.argv[1])
