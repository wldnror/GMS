#!/usr/bin/env python3
# coding: utf-8

import sys, time
from pymodbus.client import ModbusTcpClient

BASE = 40001

def off(r): return r - BASE
def ip2regs(ip):
    a,b,c,d = map(int, ip.split('.'))
    return [(a<<8)|b, (c<<8)|d]

def usage():
    print(f"Usage: {sys.argv[0]} <host> <codes> [tftp_ip]")
    print(" codes: combination of digits 1..4")
    print("   1 read-version")
    print("   2 set-tftp (needs IP)")
    print("   3 upgrade")
    print("   4 zero-cal")
    sys.exit(1)

if len(sys.argv) < 3:
    usage()

host    = sys.argv[1]
codes   = sys.argv[2]
tftp_ip = sys.argv[3] if '2' in codes and len(sys.argv)>3 else None
unit    = 1

cli = ModbusTcpClient(host, port=502, timeout=5)
if not cli.connect():
    print(f"[Error] {host}:502 연결 실패"); sys.exit(1)

# 1) read version
if '1' in codes:
    rr = cli.read_holding_registers(off(40022), 1, slave=unit)
    print("버전:", rr.registers[0] if not rr.isError() else "Error")

# 2) set tftp
if '2' in codes:
    if not tftp_ip:
        print("[Error] TFTP IP 인자 필요"); sys.exit(1)
    wr = cli.write_registers(off(40088), ip2regs(tftp_ip), slave=unit)
    print("TFTP IP 설정:", "OK" if not wr.isError() else wr)
    time.sleep(1)

# 3) upgrade
if '3' in codes:
    wr = cli.write_register(off(40091), 1, slave=unit)
    print("업그레이드 시작:", "OK" if not wr.isError() else wr)
    print("→ 재부팅 대기(약 10초)…")
    time.sleep(12)

# 4) zero-cal
if '4' in codes:
    wr = cli.write_register(off(40092), 1, slave=unit)
    print("Zero Calibration:", "OK" if not wr.isError() else wr)
    time.sleep(1)

cli.close()
