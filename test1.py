#!/usr/bin/env python3
# coding: utf-8

import sys
import time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException

# ————————————————
# 설정
# ————————————————
HOST    = sys.argv[1] if len(sys.argv) > 1 else '192.168.0.100'
PORT    = 502
UNIT    = 1           # 슬레이브 ID
TIMEOUT = 5           # 초
BASE    = 40001       # 레지스터 베이스

def to_offset(reg): 
    return reg - BASE

def ip_to_regs(ip):
    a,b,c,d = map(int, ip.split('.'))
    return [(a<<8)|b, (c<<8)|d]

# ————————————————
# 메인
# ————————————————
if __name__ == "__main__":
    client = ModbusTcpClient(HOST, port=PORT, timeout=TIMEOUT)
    if not client.connect():
        print(f"[Error] {HOST}:{PORT} 연결 실패")
        sys.exit(1)

    try:
        # 1) 버전 읽기 (40022)
        rr = client.read_holding_registers(to_offset(40022), 1, slave=UNIT)
        if rr.isError():
            print("[Error] 버전 읽기 실패:", rr)
        else:
            print("장비 버전:", rr.registers[0])

        # 잠깐 대기
        time.sleep(0.1)

        # 2) TFTP IP 쓰기 (40088~40089)
        regs = ip_to_regs('109.3.55.2')
        wr = client.write_registers(to_offset(40088), regs, slave=UNIT)
        if wr.isError():
            print("[Error] TFTP IP 쓰기 실패:", wr)
        else:
            print("TFTP IP 설정 완료")
            time.sleep(1)

        # 3) 업그레이드 시작 (40091)
        wr = client.write_register(to_offset(40091), 1, slave=UNIT)
        if wr.isError():
            print("[Error] 업그레이드 시작 실패:", wr)
        else:
            print("업그레이드 시작 명령 전송 완료")

        time.sleep(0.1)

        # 4) 제로 캘리브레이션 (40092)
        wr = client.write_register(to_offset(40092), 1, slave=UNIT)
        if wr.isError():
            print("[Error] Zero Calibration 실패:", wr)
        else:
            print("Zero Calibration 명령 전송 완료")

    finally:
        client.close()
