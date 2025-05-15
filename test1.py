#!/usr/bin/env python3
from pymodbus.client import ModbusTcpClient
from pymodbus.transaction import ModbusRtuFramer
import sys

def test_read(ip, port=502, slave_id=1):
    # RTU 프레이머 지정
    client = ModbusTcpClient(ip, port=port, timeout=3, framer=ModbusRtuFramer)
    if not client.connect():
        print(f"[Error] TCP 연결 실패: {ip}:{port}")
        return

    try:
        addr = 40022 - 1
        resp = client.read_holding_registers(addr, 3, slave=slave_id)
        if resp.isError():
            print(f"[Error] 읽기 실패: {resp}")
        else:
            print(f"[OK] 레지스터 40022~40024 값: {resp.registers}")
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 test_read.py <IP> [SLAVE_ID]")
        sys.exit(1)

    ip = sys.argv[1]
    slave = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    test_read(ip, slave_id=slave)
