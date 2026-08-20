"""Pure helpers shared by the GMS user interfaces.

This module intentionally has no Raspberry Pi or Tkinter dependencies so its
behaviour can be tested on any machine.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections.abc import Sequence
from typing import Any

_REGISTER_BASE = 40001
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def register_offset(address: int, *, base: int = _REGISTER_BASE) -> int:
    """Translate a documented 4xxxx register number into a zero-based offset."""
    address = int(address)
    base = int(base)
    offset = address - base
    if offset < 0:
        raise ValueError(f"register {address} is below base {base}")
    return offset


def register_value(registers: Sequence[int], address: int, *, base: int = _REGISTER_BASE) -> int:
    """Return one register using its documented 4xxxx address."""
    index = register_offset(address, base=base)
    try:
        return int(registers[index])
    except IndexError as exc:
        raise ValueError(
            f"register {address} is not present: received {len(registers)} register(s)"
        ) from exc


def validate_ipv4(value: str) -> str:
    """Return a canonical IPv4 string or raise ``ValueError``."""
    try:
        address = ipaddress.ip_address((value or "").strip())
    except ValueError as exc:
        raise ValueError(f"올바르지 않은 IPv4 주소입니다: {value!r}") from exc
    if address.version != 4:
        raise ValueError(f"IPv4 주소만 사용할 수 있습니다: {value!r}")
    return str(address)


def engineering_value(milliamp: float, full_scale: float) -> float:
    """Convert a 4–20 mA reading to a clamped engineering-scale value."""
    milliamp = float(milliamp)
    full_scale = float(full_scale)
    if not math.isfinite(milliamp) or not math.isfinite(full_scale):
        raise ValueError("non-finite analog value")
    if full_scale < 0:
        raise ValueError("full_scale must be non-negative")
    value = ((milliamp - 4.0) / 16.0) * full_scale
    return max(0.0, min(value, full_scale))


def display_value(raw_engineering_value: float, gas_type: str) -> float:
    """Return the numeric value shown on the four-digit display.

    HMDS uses an implied decimal place: raw 2640 is displayed as 264.0.
    """
    value = float(raw_engineering_value)
    return value / 10.0 if gas_type == "HMDS" else value


def parse_numeric(value: Any) -> float | None:
    """Extract the first finite number from a log value.

    Supports integers/floats as well as values such as
    ``"123.4 (4.123mA, HMDS, PWR=ON)"``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    match = _NUMBER_RE.search(str(value))
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def normalize_box_id(kind: str, box_id: Any) -> str:
    """Normalize ``0``, ``"0"`` or ``"modbus_0"`` to one stable key."""
    kind = str(kind).strip().lower()
    text = str(box_id).strip()
    prefix = f"{kind}_"
    while text.startswith(prefix):
        text = text[len(prefix) :]
    return f"{prefix}{text}"
