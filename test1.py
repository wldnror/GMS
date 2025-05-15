#!/usr/bin/env python3
# coding: utf-8

import sys, time
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException

BASE = 40001
UNIT = 1
HOST = None

def off(reg): 
    return reg - BASE

def ip2regs(ip):
    a,b,c,d = map(int, ip.split('.'))
    return [(a<<8)|b, (c<<8)|d]

def connect():
    cli = ModbusTcpClient(HOST, port=502, timeout=5)
    if not cli.connect():
        print(f"[Error] {HOST}:502 연결 실패")
        sys.exit(1)
    return cli

def safe_read(cli, reg):
    try:
        return cli.read_holding_registers(off(reg), 1, slave=UNIT)
    except (ConnectionException, ModbusIOException):
        cli.close()
        time.sleep(1)
        cli = connect()
        return cli.read_holding_registers(off(reg), 1, slave=UNIT)

def safe_write(cli, reg, val):
    try:
        return cli.write_register(off(reg), val, slave=UNIT)
    except (ConnectionException, ModbusIOException):
        cli.close()
        time.sleep(1)
        cli = connect()
        return cli.write_register(off(reg), val, slave=UNIT)

def usage():
    print(f"Usage: {sys.argv[0]} <host> <code> [tftp_ip]")
    print(" code:")
    print("  1 : read-version")
    print("  2 : set-tftp   (needs IP)")
    print("  3 : upgrade")
    print("  4 : zero-cal")
    sys.exit(1)

if len(sys.argv) < 3:
    usage()

HOST = sys.argv[1]
code = sys.argv[2]
tftp = sys.argv[3] if len(sys.argv) > 3 else None

if code not in ('1','2','3','4'):
    print("[Error] 잘못된 코드")
    usage()

cli = connect()

if code == '1':
    rr = safe_read(cli, 40022)
    print("버전:", rr.registers[0] if not rr.isError() else "Error")

elif code == '2':
    if not tftp:
        print("[Error] TFTP IP 인자 필요")
        usage()
    regs = ip2regs(tftp)
    # write multiple registers 40088~40089
    wr = cli.write_registers(off(40088), regs, slave=UNIT)
    print("TFTP IP 설정:", "OK" if not wr.isError() else wr)

elif code == '3':
    # 1) 업그레이드 시작
    wr = safe_write(cli, 40091, 1)
    if wr.isError():
        print("업그레이드 시작 실패:", wr)
        sys.exit(1)
    print("업그레이드 시작: OK")
    print("→ 진행 상태를 폴링합니다…\n")

    # 2) 상태·진행률 폴링
    while True:
        rr_stat = safe_read(cli, 40023)
        if rr_stat.isError():
            print("상태 읽기 에러, 재시도…")
            time.sleep(1)
            continue

        st = rr_stat.registers[0]
        done = bool(st & 0x0001)
        fail = bool(st & 0x0002)
        in_prog = bool(st & 0x0004)
        err_code = (st >> 8) & 0xFF

        rr_prog = safe_read(cli, 40024)
        if rr_prog.isError():
            print("진행률 읽기 에러, 재시도…")
            time.sleep(1)
            continue

        pv = rr_prog.registers[0]
        progress = pv & 0xFF
        remain   = (pv >> 8) & 0xFF

        # 한 줄로 상태 표시
        status = ("완료" if done else "실패" if fail else "진행중" if in_prog else "대기")
        print(f"\r[{status}] {progress:3d}% 남은시간 {remain:3d}s 에러코드 {err_code}", end='')

        if done or fail:
            print()  # 줄바꿈
            print("업그레이드", "성공" if done else "실패")
            break

        time.sleep(1)

elif code == '4':
    wr = safe_write(cli, 40092, 1)
    print("Zero Calibration:", "OK" if not wr.isError() else wr)

cli.close()
