#!/usr/bin/env python3
# coding: utf-8

import sys
import time
import argparse
from pymodbus.client import ModbusTcpClient

# ————————————————
# 기본 설정
# ————————————————
BASE_REG = 40001

def to_offset(reg):
    return reg - BASE_REG

def ip_to_regs(ip):
    a,b,c,d = map(int, ip.split('.'))
    return [(a<<8)|b, (c<<8)|d]

def main():
    parser = argparse.ArgumentParser(
        description="GDS Modbus TCP 제어 스크립트")
    parser.add_argument("host", help="감지기 IP 또는 TFTP 서버 IP")
    parser.add_argument("--unit", "-u", type=int, default=1,
                        help="Modbus 슬레이브 ID (기본 1)")
    parser.add_argument("--read-version", "-r", action="store_true",
                        help="장비 버전(40022) 읽기")
    parser.add_argument("--set-tftp", metavar="IP",
                        help="TFTP 서버 IP를 40088~40089에 설정")
    parser.add_argument("--upgrade", action="store_true",
                        help="펌웨어 업그레이드 시작 (40091=1)")
    parser.add_argument("--zero-cal", action="store_true",
                        help="Zero Calibration 실행 (40092=1)")
    parser.add_argument("--timeout", type=float, default=5,
                        help="TCP 응답 타임아웃(초, 기본 5)")
    args = parser.parse_args()

    client = ModbusTcpClient(args.host, port=502, timeout=args.timeout)
    if not client.connect():
        print(f"[Error] {args.host}:502 연결 실패")
        sys.exit(1)

    try:
        # 1) 버전 읽기
        if args.read_version:
            rr = client.read_holding_registers(
                to_offset(40022), 1, slave=args.unit)
            if rr.isError():
                print("[Error] 버전 읽기 실패:", rr)
            else:
                print("장비 버전:", rr.registers[0])

        # 2) TFTP IP 설정
        if args.set_tftp:
            regs = ip_to_regs(args.set_tftp)
            wr = client.write_registers(
                to_offset(40088), regs, slave=args.unit)
            if wr.isError():
                print("[Error] TFTP IP 쓰기 실패:", wr)
            else:
                print(f"TFTP IP 설정 완료: {args.set_tftp}")
                time.sleep(1)

        # 3) 업그레이드 시작
        if args.upgrade:
            wr = client.write_register(
                to_offset(40091), 1, slave=args.unit)
            if wr.isError():
                print("[Error] 업그레이드 시작 실패:", wr)
            else:
                print("업그레이드 시작 명령 전송 완료")
                print("→ 플래시 기록·재부팅 대기(약 10초)…")
                time.sleep(12)

        # 4) Zero Calibration
        if args.zero_cal:
            wr = client.write_register(
                to_offset(40092), 1, slave=args.unit)
            if wr.isError():
                print("[Error] Zero Calibration 실패:", wr)
            else:
                print("Zero Calibration 명령 전송 완료")
                time.sleep(1)

    finally:
        client.close()

if __name__ == "__main__":
    main()
