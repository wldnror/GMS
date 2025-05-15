#!/usr/bin/env python3
# coding: utf-8

import sys
import time
from pymodbus.client import ModbusTcpClient

# ————————————————
# 설정
# ————————————————
HOST = sys.argv[1]            # 감지기 IP
UNIT = 1                      # 슬레이브 ID
BASE = 40001                  # 레지스터 베이스

def to_offset(reg):
    return reg - BASE

if __name__ == "__main__":
    client = ModbusTcpClient(HOST, port=502, timeout=5)
    if not client.connect():
        print(f"[Error] {HOST} 연결 실패")
        sys.exit(1)

    try:
        # Zero Calibration (40092, BIT0=1)
        wr = client.write_register(to_offset(40092), 1, slave=UNIT)
        if wr.isError():
            print("[Error] Zero Calibration 실패:", wr)
        else:
            print("Zero Calibration 명령 전송 완료")
            # 장비 처리 시간 필요하면 잠시 대기
            time.sleep(1)
    finally:
        client.close()
