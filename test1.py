#!/usr/bin/env python3
"""Command-line diagnostic for the extended detector Modbus registers."""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Any

from core_utils import ip_to_register_words
from gms_core import validate_tftp_ipv4

BASE = 40001
COMMAND_ALIASES = {
    "1": "read-version",
    "2": "set-tftp",
    "3": "upgrade",
    "4": "zero-cal",
    "read-version": "read-version",
    "set-tftp": "set-tftp",
    "upgrade": "upgrade",
    "zero-cal": "zero-cal",
}
DESTRUCTIVE_COMMANDS = {"set-tftp", "upgrade", "zero-cal"}


def offset(register: int) -> int:
    return int(register) - BASE


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("0보다 큰 값이어야 합니다")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GMS 확장 Modbus 레지스터 진단 도구")
    parser.add_argument("host", help="검출기 IPv4 주소 또는 호스트명")
    parser.add_argument(
        "command",
        choices=tuple(COMMAND_ALIASES),
        help="1/read-version, 2/set-tftp, 3/upgrade, 4/zero-cal",
    )
    parser.add_argument("tftp_ip", nargs="?", help="set-tftp에서 사용할 TFTP IPv4")
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=300.0,
        help="upgrade 상태 폴링 전체 제한 시간(초, 기본: 300)",
    )
    parser.add_argument(
        "--request-timeout",
        type=positive_float,
        default=5.0,
        help="개별 Modbus 요청 제한 시간(초, 기본: 5)",
    )
    parser.add_argument(
        "--poll-interval",
        type=positive_float,
        default=1.0,
        help="upgrade 폴링 간격(초, 기본: 1)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="장치 상태를 변경하는 명령을 명시적으로 승인",
    )
    return parser


def connect(host: str, request_timeout: float) -> Any:
    # Keep the import side-effect-free for hosts used only for source checks.
    from pymodbus.client import ModbusTcpClient

    client = ModbusTcpClient(
        host,
        port=502,
        timeout=request_timeout,
        retries=0,
    )
    if not client.connect():
        client.close()
        raise ConnectionError(f"{host}:502 연결 실패")
    return client


def read_registers(client: Any, register: int, count: int) -> Any:
    return client.read_holding_registers(
        address=offset(register),
        count=count,
    )


def require_registers(response: Any, expected: int, context: str) -> list[int]:
    is_error = getattr(response, "isError", None)
    if response is None or not callable(is_error):
        raise RuntimeError(f"{context} 응답 형식 오류: {response}")
    if is_error():
        raise RuntimeError(f"{context} Modbus 오류: {response}")
    registers = getattr(response, "registers", None)
    if not isinstance(registers, (list, tuple)) or len(registers) < expected:
        actual = len(registers) if isinstance(registers, (list, tuple)) else 0
        raise RuntimeError(
            f"{context} 응답 길이 오류: {expected}개 필요, {actual}개 수신"
        )
    return [int(value) for value in registers[:expected]]


def require_write(response: Any, context: str) -> None:
    is_error = getattr(response, "isError", None)
    if response is None or not callable(is_error):
        raise RuntimeError(f"{context} 응답 형식 오류: {response}")
    if is_error():
        raise RuntimeError(f"{context} 실패: {response}")


def reconnect(host: str, client: Any) -> Any:
    try:
        client.close()
    except Exception:
        pass
    if not client.connect():
        raise ConnectionError(f"{host}:502 재연결 실패")
    return client


def read_with_reconnect(
    host: str,
    client: Any,
    register: int,
    count: int,
) -> tuple[Any, Any]:
    """Retry one idempotent read after reconnect; writes are never retried."""

    try:
        return client, read_registers(client, register, count)
    except Exception:
        client = reconnect(host, client)
        return client, read_registers(client, register, count)


def poll_upgrade(
    host: str,
    client: Any,
    timeout: float,
    poll_interval: float,
) -> tuple[Any, bool]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            client, response = read_with_reconnect(
                host,
                client,
                40023,
                2,
            )
            status_word, progress_word = require_registers(
                response, 2, "업그레이드 상태"
            )
            last_error = None
        except Exception as exc:
            last_error = exc
            print(f"\n상태 읽기 오류: {exc}", file=sys.stderr)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval, remaining))
            continue

        done = bool(status_word & 0x0001)
        failed = bool(status_word & 0x0002)
        running = bool(status_word & 0x0004)
        error_code = (status_word >> 8) & 0xFF
        progress = progress_word & 0xFF
        remain = (progress_word >> 8) & 0xFF
        state = (
            "실패" if failed else "완료" if done else "진행중" if running else "대기"
        )
        print(
            f"\r[{state}] {progress:3d}% 남은시간 {remain:3d}s 에러코드 {error_code}",
            end="",
            flush=True,
        )
        if done or failed:
            print()
            return client, done and not failed

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))

    detail = f" (마지막 오류: {last_error})" if last_error else ""
    raise TimeoutError(f"업그레이드 상태 확인이 {timeout:g}초를 초과했습니다{detail}")


def run(args: argparse.Namespace) -> int:
    command = COMMAND_ALIASES[args.command]
    if command in DESTRUCTIVE_COMMANDS and not args.yes:
        raise PermissionError(
            f"{command}는 장치 상태를 변경합니다. 검토 후 --yes를 추가하세요."
        )
    if command == "set-tftp" and not args.tftp_ip:
        raise ValueError("set-tftp에는 TFTP IPv4 주소가 필요합니다")
    if command != "set-tftp" and args.tftp_ip:
        raise ValueError("TFTP IPv4 주소는 set-tftp에서만 사용합니다")
    tftp_ip = validate_tftp_ipv4(args.tftp_ip) if command == "set-tftp" else None

    client = connect(args.host, args.request_timeout)
    try:
        if command == "read-version":
            response = read_registers(client, 40022, 1)
            version = require_registers(response, 1, "버전 읽기")[0]
            print(f"버전: {version}")
            return 0

        if command == "set-tftp":
            words = list(ip_to_register_words(tftp_ip))
            if len(words) != 2:
                raise RuntimeError("TFTP IPv4 변환 결과가 2개 레지스터가 아닙니다")
            response = client.write_registers(
                address=offset(40088),
                values=words,
            )
            require_write(response, "TFTP IP 설정")
            print("TFTP IP 설정: OK")
            return 0

        if command == "upgrade":
            # Never retry this non-idempotent command automatically.
            response = client.write_register(address=offset(40091), value=1)
            require_write(response, "업그레이드 시작")
            print("업그레이드 시작: OK\n→ 진행 상태를 폴링합니다…")
            client, succeeded = poll_upgrade(
                args.host,
                client,
                args.timeout,
                args.poll_interval,
            )
            print(f"업그레이드 {'성공' if succeeded else '실패'}")
            return 0 if succeeded else 1

        # Never retry this non-idempotent command automatically.
        response = client.write_register(address=offset(40092), value=1)
        require_write(response, "Zero Calibration")
        print("Zero Calibration: OK")
        return 0
    finally:
        client.close()


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except (
        ConnectionError,
        ImportError,
        OSError,
        PermissionError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
