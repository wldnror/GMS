"""Backward-compatible pure helpers shared by GMS modules."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from typing import Any

from gms_core import (
    display_value,
    engineering_value,
    parse_numeric,
    register_offset,
    register_value as _register_value,
    validate_ipv4,
)

ERROR_BIT_TO_DISPLAY = {
    0: "E-10",
    1: "E-22",
    2: "E-12",
    3: "E-23",
}


def register_index(register: int, base: int = 40001) -> int:
    return register_offset(register, base=base)


def register_value(registers: Sequence[int], register: int, base: int = 40001) -> int:
    return _register_value(registers, register, base=base)


def decode_error_register(value: int) -> str:
    raw = int(value) & 0xFFFF
    for bit, label in ERROR_BIT_TO_DISPLAY.items():
        if raw & (1 << bit):
            return label
    return ""


def scale_4_20ma(milliamp: float, full_scale: float) -> float:
    return engineering_value(milliamp, full_scale)


def display_engineering_value(gas_type: str, raw_value: float) -> float:
    return display_value(raw_value, gas_type)


def format_engineering_value(gas_type: str, raw_value: float) -> str:
    value = display_engineering_value(gas_type, raw_value)
    if gas_type == "HMDS":
        return f"{value:4.1f}"[-5:]
    return f"{int(round(value)):>4}"[-4:]


def numeric_from_log_value(value: Any) -> float | None:
    return parse_numeric(value)


def format_log_extra(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return f"0x{value & 0xFFFF:04X}"
    return str(value)


def normalize_ipv4(value: str) -> str:
    return validate_ipv4(value)


def ip_to_register_words(value: str) -> tuple[int, int]:
    address = ipaddress.IPv4Address(normalize_ipv4(value))
    octets = address.packed
    return ((octets[0] << 8) | octets[1], (octets[2] << 8) | octets[3])


def registers_to_ipv4(registers: Iterable[int]) -> str:
    values = list(registers)
    if len(values) != 2:
        raise ValueError("IPv4 conversion requires exactly two registers")
    word1, word2 = (int(values[0]) & 0xFFFF, int(values[1]) & 0xFFFF)
    return str(
        ipaddress.IPv4Address(
            bytes(
                (
                    (word1 >> 8) & 0xFF,
                    word1 & 0xFF,
                    (word2 >> 8) & 0xFF,
                    word2 & 0xFF,
                )
            )
        )
    )


def battery_percentage(voltage: float, cell_count: int = 6) -> int:
    if int(cell_count) <= 0:
        raise ValueError("cell_count must be positive")
    cell_voltage = max(0.0, float(voltage)) / int(cell_count)
    if cell_voltage >= 4.2:
        return 100
    if cell_voltage > 3.7:
        return max(0, min(100, int(round((cell_voltage - 3.7) / 0.5 * 50 + 50))))
    if cell_voltage > 3.0:
        return max(0, min(100, int(round((cell_voltage - 3.0) / 0.7 * 50))))
    return 0
