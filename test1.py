#!/usr/bin/env python3
# coding: utf-8
"""Small command-line utility for the extended detector registers."""

from __future__ import annotations

import sys
import time

from pymodbus.client import ModbusTcpClient

from core_utils import ip_to_register_words

BASE = 40001

def offset(register: int) -> int:
    return int(register) - BASE


def usage() -> None:
    print(f"Usage: {sys.argv[0]} <host> <code> [tftp_ip]")
    print(" code:")
    print("  1 : read-version")
    print("  2 : set-tftp   (needs IP)")
    print("  3 : upgrade")
    print("  4 : zero-cal")
    raise SystemExit(1)


def connect(host: str) -> ModbusTcpClient:
    client = ModbusTcpClient(host, port=502, timeout=5)
    if not client.connect():
        raise ConnectionError(f"{host}:502 연결 실패")
    return client


def read_one(client: ModbusTcpClient, register: int):
    return client.read_holding_registers(
        address=offset(register), count=1
    )


def write_one(client: ModbusTcpClient, register: int, value: int):
    return client.write_register(
        address=offset(register), value=value
    )


def reconnect(host: str, client: ModbusTcpClient) -> ModbusTcpClient:
    try:
        client.close()
    except Exception:
        pass
    time.sleep(1)
    return connect(host)


def safe_read(host: str, client: ModbusTcpClient, register: int):
    try:
        response = read_one(client, register)
        return client, response
    except Exception:
        client = reconnect(host, client)
        return client, read_one(client, register)


def safe_write(host: str, client: ModbusTcpClient, register: int, value: int):
    try:
        response = write_one(client, register, value)
        return client, response
    except Exception:
        client = reconnect(host, client)
        return client, write_one(client, register, value)


def main() -> None:
    if len(sys.argv) < 3:
        usage()
    host = sys.argv[1]
    code = sys.argv[2]
    tftp_ip = sys.argv[3] if len(sys.argv) > 3 else None
    if code not in {"1", "2", "3", "4"}:
        usage()

    client = connect(host)
    try:
        if code == "1":
            client, response = safe_read(host, client, 40022)
            print("버전:", response.registers[0] if not response.isError() else response)
        elif code == "2":
            if not tftp_ip:
                usage()
            words = list(ip_to_register_words(tftp_ip))
            response = client.write_registers(
                address=offset(40088), values=words
            )
            print("TFTP IP 설정:", "OK" if not response.isError() else response)
        elif code == "3":
            client, response = safe_write(host, client, 40091, 1)
            if response.isError():
                raise RuntimeError(f"업그레이드 시작 실패: {response}")
            print("업그레이드 시작: OK\n→ 진행 상태를 폴링합니다…")
            while True:
                try:
                    client, status_response = safe_read(host, client, 40023)
                    client, progress_response = safe_read(host, client, 40024)
                except Exception as exc:
                    print(f"\n상태 읽기 오류: {exc}")
                    time.sleep(1)
                    continue
                if status_response.isError() or progress_response.isError():
                    time.sleep(1)
                    continue
                status_word = status_response.registers[0]
                progress_word = progress_response.registers[0]
                done = bool(status_word & 0x0001)
                failed = bool(status_word & 0x0002)
                running = bool(status_word & 0x0004)
                error_code = (status_word >> 8) & 0xFF
                progress = progress_word & 0xFF
                remain = (progress_word >> 8) & 0xFF
                state = "완료" if done else "실패" if failed else "진행중" if running else "대기"
                print(
                    f"\r[{state}] {progress:3d}% 남은시간 {remain:3d}s 에러코드 {error_code}",
                    end="",
                    flush=True,
                )
                if done or failed:
                    print(f"\n업그레이드 {'성공' if done else '실패'}")
                    break
                time.sleep(1)
        else:
            client, response = safe_write(host, client, 40092, 1)
            print("Zero Calibration:", "OK" if not response.isError() else response)
    finally:
        client.close()


if __name__ == "__main__":
    main()
