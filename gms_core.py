"""Pure helpers shared by the GMS user interfaces.

This module intentionally has no Raspberry Pi or Tkinter dependencies so its
behaviour can be tested on any machine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_REGISTER_BASE = 40001
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 310_000
_MIN_PASSWORD_ITERATIONS = 100_000
_MAX_PASSWORD_ITERATIONS = 1_000_000


def register_offset(address: int, *, base: int = _REGISTER_BASE) -> int:
    """Translate a documented 4xxxx register number into a zero-based offset."""
    address = int(address)
    base = int(base)
    offset = address - base
    if offset < 0:
        raise ValueError(f"register {address} is below base {base}")
    return offset


def register_value(
    registers: Sequence[int], address: int, *, base: int = _REGISTER_BASE
) -> int:
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


def validate_tftp_ipv4(value: str) -> str:
    """Validate a detector-reachable IPv4 address for the TFTP server.

    A detector cannot reach a TFTP server through loopback, unspecified, or
    multicast addresses.  Rejecting those values prevents an upgrade command
    from being sent with the historical ``127.0.0.1`` fallback.
    """
    address = ipaddress.IPv4Address(validate_ipv4(value))
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError(f"TFTP 서버로 사용할 수 없는 IPv4 주소입니다: {value!r}")
    if address == ipaddress.IPv4Address("255.255.255.255"):
        raise ValueError(f"TFTP 서버로 사용할 수 없는 IPv4 주소입니다: {value!r}")
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


def analog_signal_fault(
    milliamp: float,
    *,
    low: float = 3.6,
    high: float = 21.0,
) -> bool:
    """Return whether a current-loop value is invalid or outside its limits."""

    try:
        value = float(milliamp)
        low_limit = float(low)
        high_limit = float(high)
    except (TypeError, ValueError):
        return True
    if not all(math.isfinite(item) for item in (value, low_limit, high_limit)):
        return True
    if low_limit >= high_limit:
        raise ValueError("analog fault low limit must be below high limit")
    return value < low_limit or value > high_limit


def ups_fault_active(
    level_percent: int,
    *,
    sensor_error: bool,
    was_fault: bool,
    low_percent: int = 20,
    clear_percent: int = 25,
) -> bool:
    """Apply low-battery hysteresis while treating sensor loss as a fault."""

    low = int(low_percent)
    clear = int(clear_percent)
    if not 0 <= low < clear <= 100:
        raise ValueError("UPS thresholds must satisfy 0 <= low < clear <= 100")
    if sensor_error:
        return True
    level = max(0, min(100, int(level_percent)))
    return level < clear if was_fault else level <= low


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


def aggregate_alarm_mode(states: Iterable[Mapping[str, Any]]) -> str:
    """Return a mode that preserves alarms and faults independently."""
    has_alarm = False
    has_fut = False
    for state in states:
        has_alarm = has_alarm or bool(state.get("active", False))
        has_fut = has_fut or bool(state.get("fut", False))
    if has_alarm and has_fut:
        return "alarm_fut"
    if has_alarm:
        return "alarm"
    if has_fut:
        return "fut"
    return "none"


def reconnect_delay(attempt: int, *, maximum: float = 30.0) -> float:
    """Return a cancellable exponential reconnect delay capped for kiosks."""
    attempt = max(1, int(attempt))
    maximum = max(1.0, float(maximum))
    return min(maximum, float(2 ** min(attempt, 10)))


def password_is_hashed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(f"{_PASSWORD_SCHEME}$")


def hash_password(
    password: str,
    *,
    iterations: int = _PASSWORD_ITERATIONS,
    salt: bytes | None = None,
) -> str:
    """Create a versioned PBKDF2 password representation for local admin PINs."""
    password = str(password)
    if not password:
        raise ValueError("password must not be empty")
    if len(password) > 64:
        raise ValueError("password must not exceed 64 characters")
    iterations = int(iterations)
    if not _MIN_PASSWORD_ITERATIONS <= iterations <= _MAX_PASSWORD_ITERATIONS:
        raise ValueError("unsupported PBKDF2 iteration count")
    salt = os.urandom(16) if salt is None else bytes(salt)
    if len(salt) < 16:
        raise ValueError("password salt must contain at least 16 bytes")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{_PASSWORD_SCHEME}${iterations}${salt_text}${digest_text}"


def verify_password(password: str, encoded: object) -> bool:
    """Verify a versioned password hash without accepting malformed costs."""
    password = str(password)
    if not password or len(password) > 64:
        return False
    if not password_is_hashed(encoded):
        return False
    try:
        scheme, iterations_text, salt_text, digest_text = str(encoded).split("$", 3)
        if scheme != _PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        if not _MIN_PASSWORD_ITERATIONS <= iterations <= _MAX_PASSWORD_ITERATIONS:
            return False

        def decode(value: str) -> bytes:
            padding = "=" * (-len(value) % 4)
            return base64.b64decode(
                value + padding,
                altchars=b"-_",
                validate=True,
            )

        salt = decode(salt_text)
        expected = decode(digest_text)
        if len(salt) < 16 or len(expected) != hashlib.sha256().digest_size:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)
