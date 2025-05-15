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
    # 1) 업그레이드 시작
    wr = cli.write_register(off(40091), 1, slave=unit)
    if wr.isError():
        print("업그레이드 시작 실패:", wr)
        cli.close()
        sys.exit(1)
    print("업그레이드 시작: OK")
    print("→ 플래시 기록·재부팅 대기(약 10초)…")
    time.sleep(12)

    # 2) 업그레이드 상태 읽기 (40023)
    rr_stat = cli.read_holding_registers(off(40023), 1, slave=unit)
    if rr_stat.isError():
        print("상태 정보 읽기 실패:", rr_stat)
    else:
        st = rr_stat.registers[0]
        print("\n[업그레이드 상태]")
        print("  성공        :", bool(st & 0x0001))
        print("  실패        :", bool(st & 0x0002))
        print("  진행 중     :", bool(st & 0x0004))
        print("  롤백 성공   :", bool(st & 0x0010))
        print("  롤백 실패   :", bool(st & 0x0020))
        print("  롤백 중     :", bool(st & 0x0040))
        err_code = (st >> 8) & 0xFF
        print(f"  에러 코드   : {err_code}")

    # 3) 다운로드 진행률 읽기 (40024)
    rr_prog = cli.read_holding_registers(off(40024), 1, slave=unit)
    if rr_prog.isError():
        print("진행 정보 읽기 실패:", rr_prog)
    else:
        pv = rr_prog.registers[0]
        progress = pv & 0xFF
        remain   = (pv >> 8) & 0xFF
        print("\n[다운로드 진행]")
        print(f"  완료율     : {progress}%")
        print(f"  남은 시간  : {remain}초")

elif code == '4':
    wr = cli.write_register(off(40092), 1, slave=unit)
    print("Zero Calibration:", "OK" if not wr.isError() else wr)

cli.close()
