#!/usr/bin/env python3
# coding: utf-8

from pymodbus.client.sync import ModbusTcpClient
import sys

# ————————————————
# 설정
# ————————————————
HOST = sys.argv[1] if len(sys.argv) > 1 else '192.168.0.100'
PORT = 502
UNIT = 1  # Modbus 슬레이브 ID

# GDS 레지스터 기준 주소
REG_BASE = 40001

def to_offset(reg_addr):
    """Modbus 주소(예: 40088)를 0-based offset으로 변환"""
    return reg_addr - REG_BASE

def ip_to_regs(ip_str):
    """'109.3.55.2' → [0x6D03, 0x3702]"""
    parts = list(map(int, ip_str.split('.')))
    return [(parts[0] << 8) | parts[1], (parts[2] << 8) | parts[3]]

# ————————————————
# 클라이언트 연결
# ————————————————
client = ModbusTcpClient(HOST, port=PORT)
if not client.connect():
    print(f"[Error] {HOST} 연결 실패")
    sys.exit(1)

try:
    # 1) 버전 정보 읽기 (40022)
    rr = client.read_holding_registers(to_offset(40022), count=1, unit=UNIT)
    if not rr.isError():
        version = rr.registers[0]
        print(f"장비 버전(Unsigned): {version}")
    else:
        print("[Error] 버전 정보 읽기 실패:", rr)

    # 2) TFTP 서버 IP 설정 (40088~40089)
    ip_regs = ip_to_regs('109.3.55.2')
    wr = client.write_registers(to_offset(40088), ip_regs, unit=UNIT)
    if wr.isError():
        print("[Error] TFTP IP 쓰기 실패:", wr)
    else:
        print("TFTP IP 설정 완료")

    # 3) 업그레이드 시작 (40091, 1:업그레이드 시작)
    wr = client.write_register(to_offset(40091), 1, unit=UNIT)
    if wr.isError():
        print("[Error] 업그레이드 명령 실패:", wr)
    else:
        print("업그레이드 시작 명령 전송 완료")

    # 4) 제로 캘리브레이션 (40092, 1:Zero Calibration)
    wr = client.write_register(to_offset(40092), 1, unit=UNIT)
    if wr.isError():
        print("[Error] Zero Cal 명령 실패:", wr)
    else:
        print("Zero Calibration 명령 전송 완료")

finally:
    client.close()
