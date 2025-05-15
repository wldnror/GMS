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
    print(f"Usage: {sys.argv[0]} <host> <code> [tftp_ip]")
    print(" code:")
    print("  1 : read-version")
    print("  2 : set-tftp   (needs IP)")
    print("  3 : upgrade")
    print("  4 : zero-cal")
    sys.exit(1)

if len(sys.argv) < 3:
    usage()

host  = sys.argv[1]
code  = sys.argv[2]
tftp  = sys.argv[3] if len(sys.argv) > 3 else None
unit  = 1

# 한 번에 하나만!
if code not in ('1','2','3','4'):
    print("[Error] 잘못된 코드")
    usage()

cli = ModbusTcpClient(host, port=502, timeout=5)
if not cli.connect():
    print(f"[Error] {host}:502 연결 실패")
    sys.exit(1)

if code == '1':
    rr = cli.read_holding_registers(off(40022), 1, slave=unit)
    print("버전:", rr.registers[0] if not rr.isError() else "Error")

elif code == '2':
    if not tftp:
        print("[Error] TFTP IP 인자 필요")
        usage()
    wr = cli.write_registers(off(40088), ip2regs(tftp), slave=unit)
    print("TFTP IP 설정:", "OK" if not wr.isError() else wr)

elif code == '3':
    wr = cli.write_register(off(40091), 1, slave=unit)
    print("업그레이드 시작:", "OK" if not wr.isError() else wr)
    print("→ 재부팅 대기(약 10초)…")
    time.sleep(12)

elif code == '4':
    wr = cli.write_register(off(40092), 1, slave=unit)
    print("Zero Calibration:", "OK" if not wr.isError() else wr)

cli.close()
