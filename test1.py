#!/usr/bin/env python3
# coding: utf-8

import sys
import time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

# ————————————————
# 설정
# ————————————————
HOST    = sys.argv[1] if len(sys.argv) > 1 else '192.168.0.100'
PORT    = 502
UNIT    = 1         # Modbus 슬레이브 ID
TIMEOUT = 5         # seconds
RETRIES = 3         # 재시도 횟수

# GDS 레지스터 기준
REG_BASE = 40001

def to_offset(reg):
    return reg - REG_BASE

def ip_to_regs(ip):
    parts = list(map(int, ip.split('.')))
    # 예: 109.3 → 0x6D03, 55.2 → 0x3702
    return [(parts[0] << 8) | parts[1], (parts[2] << 8) | parts[3]]

# ————————————————
# Modbus 호출 래퍼
# ————————————————
def safe_call(client, func, *args, **kwargs):
    for attempt in range(1, RETRIES+1):
        # 깨진 세션 방지: 매번 새로 연결
        client.close()
        if not client.connect():
            print(f"[Attempt {attempt}] {HOST}:{PORT} 연결 실패")
            continue

        try:
            rr = func(*args, **kwargs)
            if rr.isError():
                print(f"[Attempt {attempt}] {func.__name__} 에러:", rr)
                time.sleep(0.5)
                continue
            return rr
        except (ConnectionException, ModbusIOException) as e:
            print(f"[Attempt {attempt}] Exception in {func.__name__}:", e)
            time.sleep(0.5)
    return None

# ————————————————
# 메인
# ————————————————
if __name__ == "__main__":
    client = ModbusTcpClient(HOST, port=PORT, timeout=TIMEOUT)

    # 1) 버전 읽기 (40022)  [oai_citation:0‡GDS Modbus TCP Address Map.docx](file-service://file-7NLPh4sWKxF84iqigo2xHC)
    rr = safe_call(client,
                   client.read_holding_registers,
                   to_offset(40022), 1, slave=UNIT)
    if rr:
        print("장비 버전:", rr.registers[0])

    # 2) TFTP IP 쓰기 (40088~40089)  [oai_citation:1‡GDS Modbus TCP Address Map.docx](file-service://file-7NLPh4sWKxF84iqigo2xHC)
    ip_regs = ip_to_regs('109.3.55.2')
    wr = safe_call(client,
                   client.write_registers,
                   to_offset(40088), ip_regs, slave=UNIT)
    if wr:
        print("TFTP IP 설정 완료, 장비 반응 대기…")
        time.sleep(2)

    # 3) 업그레이드 시작 (40091)  [oai_citation:2‡GDS Modbus TCP Address Map.docx](file-service://file-7NLPh4sWKxF84iqigo2xHC)
    wr = safe_call(client,
                   client.write_register,
                   to_offset(40091), 1, slave=UNIT)
    if wr:
        print("업그레이드 시작 명령 전송 완료")

    # 4) 제로 캘리브레이션 (40092)  [oai_citation:3‡GDS Modbus TCP Address Map.docx](file-service://file-7NLPh4sWKxF84iqigo2xHC)
    wr = safe_call(client,
                   client.write_register,
                   to_offset(40092), 1, slave=UNIT)
    if wr:
        print("Zero Calibration 명령 전송 완료")

    client.close()
