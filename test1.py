#!/usr/bin/env python3
from pymodbus.client.sync import ModbusTcpClient
import time, sys

def test_zero_cal(ip, slave_id=1):
    client = ModbusTcpClient(ip, port=502, timeout=3)
    if not client.connect():
        print(f"[Error] {ip}:502 연결 실패")
        return

    try:
        # 1) 정상 읽기 확인
        resp1 = client.read_holding_registers(40001-1, 1, unit=slave_id)
        print("Read 40001:", getattr(resp1, "registers", resp1))

        # 2) Zero Cal 명령 쓰기 (Function 0x06, Reg 40092)
        wr = client.write_register(40092-1, 1, unit=slave_id)
        print("Write 40092 (Zero Cal):", wr)

        # 3) 센서가 리부팅/처리할 시간을 확보
        time.sleep(2)

        # 4) 쓰기 후 상태 읽기
        resp2 = client.read_holding_registers(40092-1, 1, unit=slave_id)
        status = resp2.registers[0] & 0x1 if not resp2.isError() else None
        print("Zero Cal 상태 (BIT0):", status)

    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test1.py <SENSOR_IP>")
    else:
        test_zero_cal(sys.argv[1])
