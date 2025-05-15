#!/usr/bin/env python3
# coding: utf-8

import sys, time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

BASE = 40001
HOST = sys.argv[1]
UNIT = 1

def off(r): return r - BASE

def connect_client():
    cli = ModbusTcpClient(HOST, port=502, timeout=5)
    if not cli.connect():
        print(f"[Error] {HOST}:502 연결 실패")
        sys.exit(1)
    return cli

def read_reg(cli, addr):
    try:
        return cli.read_holding_registers(off(addr), 1, slave=UNIT)
    except (ConnectionException, ModbusIOException):
        # 재연결 시도
        cli.close()
        time.sleep(1)
        cli = connect_client()
        return cli.read_holding_registers(off(addr), 1, slave=UNIT)

def write_reg(cli, addr, value):
    try:
        return cli.write_register(off(addr), value, slave=UNIT)
    except (ConnectionException, ModbusIOException):
        cli.close()
        time.sleep(1)
        cli = connect_client()
        return cli.write_register(off(addr), value, slave=UNIT)

if len(sys.argv) != 3 or sys.argv[2] != '3':
    print("Usage for upgrade: python3 test1.py <host> 3")
    sys.exit(1)

cli = connect_client()

# 1) 업그레이드 시작
wr = write_reg(cli, 40091, 1)
if wr.isError():
    print("업그레이드 시작 실패:", wr)
    sys.exit(1)
print("업그레이드 시작: OK")
print("→ 진행 상태를 폴링합니다…")

# 2) 상태·진행률 폴링
while True:
    # 상태 읽기
    rr_stat = read_reg(cli, 40023)
    if rr_stat.isError():
        print("상태 읽기 에러, 재시도…")
        time.sleep(1)
        continue
    st = rr_stat.registers[0]
    done = bool(st & 0x0001)
    fail = bool(st & 0x0002)
    in_progress = bool(st & 0x0004)

    # 진행률 읽기
    rr_prog = read_reg(cli, 40024)
    if rr_prog.isError():
        print("진행률 읽기 에러, 재시도…")
        time.sleep(1)
        continue
    pv = rr_prog.registers[0]
    progress = pv & 0xFF
    remain   = (pv >> 8) & 0xFF

    # 화면 출력
    status = "진행중" if in_progress else ("완료" if done else ("실패" if fail else "대기"))
    print(f"[{status}] {progress:3d}% 남은시간 {remain:3d}s", end='\r')

    if done or fail:
        print()
        print("업그레이드", "성공했습니다." if done else "실패했습니다.")
        break

    time.sleep(1)

cli.close()
