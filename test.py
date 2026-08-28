#!/usr/bin/env python3
"""Read-only ADS1115 current-loop diagnostic.

The historical version opened the I2C bus and a matplotlib window during
import.  This CLI is intentionally import-safe: hardware is imported and
opened only from :func:`main` after the command line has been validated.
"""

from __future__ import annotations

import argparse
import math
import time
from typing import Any, Iterable

DEFAULT_ADDRESSES = (0x48, 0x49, 0x4B)
GAIN = 2 / 3
ADC_FULL_SCALE_VOLTS = 6.144
SHUNT_OHMS = 250.0


def i2c_address(value: str) -> int:
    """Parse and validate a seven-bit I2C address."""

    try:
        address = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"잘못된 I2C 주소: {value}") from exc
    if not 0x03 <= address <= 0x77:
        raise argparse.ArgumentTypeError("I2C 주소는 0x03~0x77 범위여야 합니다")
    return address


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("0보다 큰 값이어야 합니다")
    return number


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0 이상의 정수여야 합니다")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ADS1115 4–20 mA 입력을 텍스트로 읽습니다(읽기 전용)."
    )
    parser.add_argument(
        "--addresses",
        nargs="+",
        type=i2c_address,
        default=list(DEFAULT_ADDRESSES),
        metavar="0x48",
        help="ADS1115 I2C 주소 목록 (기본: 0x48 0x49 0x4B)",
    )
    parser.add_argument("--bus", type=int, default=1, help="I2C 버스 번호 (기본: 1)")
    parser.add_argument(
        "--samples",
        type=non_negative_int,
        default=1,
        help="샘플 수. 0이면 Ctrl+C까지 계속 읽습니다 (기본: 1)",
    )
    parser.add_argument(
        "--interval",
        type=positive_float,
        default=1.0,
        help="샘플 간격(초, 기본: 1.0)",
    )
    parser.add_argument(
        "--full-scale-ma",
        type=positive_float,
        default=20.0,
        help="100%%로 표시할 전류(mA, 기본: 20)",
    )
    return parser


def open_adcs(driver: Any, addresses: Iterable[int], bus: int) -> dict[int, Any]:
    adcs: dict[int, Any] = {}
    for address in addresses:
        try:
            adcs[address] = driver.ADS1115(address=address, busnum=bus)
            print(f"[OK] ADS1115 0x{address:02X} 초기화")
        except Exception as exc:  # Hardware/SMBus exceptions vary by driver.
            print(f"[FAIL] ADS1115 0x{address:02X} 초기화: {exc}")
    return adcs


def read_rows(adcs: dict[int, Any], full_scale_ma: float) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for address, adc in adcs.items():
        for channel in range(4):
            label = f"0x{address:02X}/CH{channel}"
            try:
                raw = adc.read_adc(channel, gain=GAIN)
                voltage = raw * ADC_FULL_SCALE_VOLTS / 32767
                milliamp = voltage / SHUNT_OHMS * 1000
                percent = min(max(milliamp / full_scale_ma * 100, 0), 100)
                rows.append((label, f"{milliamp:8.3f} mA", f"{percent:6.2f}%"))
            except Exception as exc:  # Keep other channels observable.
                rows.append((label, "READ ERROR", str(exc)))
    return rows


def print_sample(number: int, rows: Iterable[tuple[str, str, str]]) -> None:
    print(f"\n--- sample {number} ---")
    for label, current, detail in rows:
        print(f"{label:10s} {current:>14s} {detail}")


def main() -> int:
    args = build_parser().parse_args()
    try:
        import Adafruit_ADS1x15  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"Adafruit-ADS1x15 패키지가 필요합니다: {exc}")
        return 2

    adcs = open_adcs(Adafruit_ADS1x15, args.addresses, args.bus)
    if not adcs:
        print("사용 가능한 ADS1115가 없습니다.")
        return 1

    sample = 0
    try:
        while args.samples == 0 or sample < args.samples:
            sample += 1
            print_sample(sample, read_rows(adcs, args.full_scale_ma))
            if args.samples == 0 or sample < args.samples:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n사용자가 진단을 중단했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
