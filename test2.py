#!/usr/bin/env python3
"""Interactive GPIO cooler diagnostic with explicit write confirmation."""

from __future__ import annotations

import argparse
import math
from typing import Any, Iterable, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="온도 값에 따라 GPIO 쿨러 출력을 시험합니다."
    )
    parser.add_argument("--pin", type=int, default=12, help="BCM GPIO 번호 (기본: 12)")
    parser.add_argument(
        "--threshold",
        type=float,
        default=50.0,
        help="쿨러를 켤 온도 임계값(기본: 50.0)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        action="append",
        help="시험 온도. 여러 번 지정 가능하며, 생략하면 대화형 입력",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="GPIO 출력 변경을 명시적으로 승인",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not math.isfinite(args.threshold):
        raise ValueError("threshold는 유한한 숫자여야 합니다")
    if args.temperature and not all(math.isfinite(value) for value in args.temperature):
        raise ValueError("temperature는 유한한 숫자여야 합니다")


def control_cooler(gpio: Any, pin: int, temperature: float, threshold: float) -> None:
    enabled = temperature >= threshold
    gpio.output(pin, gpio.HIGH if enabled else gpio.LOW)
    state = "켭니다" if enabled else "끕니다"
    comparison = "이상" if enabled else "미만"
    print(f"온도 {temperature:g}°C는 {threshold:g}°C {comparison}: 쿨러를 {state}.")


def interactive_temperatures() -> Iterable[float]:
    while True:
        value = input("온도를 입력하세요 (종료: exit): ").strip()
        if value.lower() in {"exit", "quit", "q"}:
            return
        try:
            yield float(value)
        except ValueError:
            print("유효한 숫자를 입력하세요.")


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_args(args)
    except ValueError as exc:
        print(f"인자 오류: {exc}")
        return 2
    if not args.yes:
        print("GPIO 출력이 변경됩니다. 배선을 확인한 뒤 --yes를 추가하세요.")
        return 2

    try:
        from RPi import GPIO  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"RPi.GPIO 패키지가 필요합니다: {exc}")
        return 2

    temperatures: Iterable[float]
    configured: Optional[int] = None
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(args.pin, GPIO.OUT, initial=GPIO.LOW)
        configured = args.pin
        temperatures = args.temperature or interactive_temperatures()
        for temperature in temperatures:
            control_cooler(GPIO, args.pin, temperature, args.threshold)
    except KeyboardInterrupt:
        print("\n사용자가 진단을 중단했습니다.")
    except Exception as exc:  # GPIO backends expose board-specific exceptions.
        print(f"GPIO 진단 오류: {exc}")
        return 1
    finally:
        if configured is not None:
            try:
                GPIO.output(configured, GPIO.LOW)
                GPIO.cleanup(configured)
                print("GPIO 출력을 LOW로 복구하고 정리했습니다.")
            except Exception as exc:
                print(f"GPIO 정리 오류: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
