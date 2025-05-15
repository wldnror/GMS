#!/usr/bin/env python3
# coding: utf-8

import sys, time
from pymodbus.client import ModbusTcpClient

# ————————————————
# 설정
# ————————————————
HOST = sys.argv[1]              # 감지기 IP
UNIT = 1                        # 슬레이브 ID
BASE = 40001                    # 레지스터 베이스

def to_offset(reg): 
    return reg - BASE

if __name__ == "__main__":
    client = ModbusTcpClient(HOST, port=502, timeout=5)
    if not client.connect():
        print(f"[Error] {HOST} 연결 실패")
        sys.exit(1)

    try:
        # 단일 레지스터 쓰기: 40091 = 1 (업그레이드 시작)  [oai_citation:1‡GDS Modbus TCP Address Map.docx](file-service://file-RJJXVkRdZu9MuWX4RNZYME)
        wr = client.write_register(to_offset(40091), 1, slave=UNIT)
        if wr.isError():
            print("[Error] 업그레이드 시작 실패:", wr)
        else:
            print("업그레이드 시작 명령 전송 완료")
            # 자동 재부팅·플래시 기록으로 약 10초간 응답 없음
            time.sleep(12)
            print("업그레이드 완료 후 재부팅 대기 완료")
    finally:
        client.close()
