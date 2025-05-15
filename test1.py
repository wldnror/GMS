#!/usr/bin/env python3
# coding: utf-8

import sys
import time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

# ————————————————
# 설정
# ————————————————
HOST = sys.argv[1] if len(sys.argv) > 1 else '192.168.0.100'
PORT = 502
UNIT = 1                # Modbus 슬레이브 ID
TIMEOUT = 5             # 초

# GDS 레지스터 기준 주소
REG_BASE = 40001

def to_offset(reg_addr):
    """Modbus 주소(예: 40088)를 0-based offset으로 변환"""
    return reg_addr - REG_BASE

def ip_to_regs(ip_str):
    """'109.3.55.2' → [109<<8|3, 55<<8|2] """
    parts = list(map(int, ip_str.split('.')))
    return [(parts[0] << 8) | parts[1], (parts[2] << 8) | parts[3]]

def ensure_connected(client):
    """연결이 끊겼으면 재시도"""
    if not client.connect():
        raise ConnectionException(f"Unable to connect to {HOST}:{PORT}")

def safe_call(func, *args, **kwargs):
    """Modbus 함수 호출용 래퍼"""
    try:
        ensure_connected(client)
        rr = func(*args, **kwargs)
        if rr.isError():
            print(f"[Error] {func.__name__} 실패:", rr)
            return None
        return rr
    except (ConnectionException, ModbusIOException) as e:
        print(f"[Exception] {func.__name__}: {e}")
        return None

# ————————————————
# 메인
# ————————————————
client = ModbusTcpClient(HOST, port=PORT, timeout=TIMEOUT)

try:
    # 1) 버전 정보 읽기 (40022)
    rr = safe_call(client.read_holding_registers,
                   to_offset(40022), 1, slave=UNIT)
    if rr:
        print(f"장비 버전(Unsigned): {rr.registers[0]}")

    # 2) TFTP 서버 IP 설정 (40088~40089)
    ip_regs = ip_to_regs('109.3.55.2')
    wr = safe_call(client.write_registers,
                   to_offset(40088), ip_regs, slave=UNIT)
    if wr:
        print("TFTP IP 설정 완료, 장비 반응 대기…")
        time.sleep(2)  # 장비가 설정 반영할 시간

    # 3) 업그레이드 시작 (40091, 1:업그레이드 시작)
    wr = safe_call(client.write_register,
                   to_offset(40091), 1, slave=UNIT)
    if wr:
        print("업그레이드 시작 명령 전송 완료")

    # 4) 제로 캘리브레이션 (40092, 1:Zero Calibration)
    wr = safe_call(client.write_register,
                   to_offset(40092), 1, slave=UNIT)
    if wr:
        print("Zero Calibration 명령 전송 완료")

finally:
    client.close()
