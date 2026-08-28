"""Small, dependency-free UI configuration helpers."""

from __future__ import annotations

import math
import os


def read_ui_scale(value: str | None = None) -> float:
    """Return a safe absolute UI scale for the kiosk panels.

    The historical layout was designed at 1.65.  Raspberry Pi installations
    can set ``GMS_UI_SCALE=1.0`` for a 1280x720 display without modifying code.
    """

    raw = os.environ.get("GMS_UI_SCALE", "1.65") if value is None else value
    try:
        scale = float(raw)
    except (TypeError, ValueError):
        return 1.65
    if not math.isfinite(scale):
        return 1.65
    return min(2.25, max(0.65, scale))


UI_SCALE = read_ui_scale()
# The seven-segment geometry historically used 1.51 while its containing
# panels used 1.65. Preserve that ratio at every selected UI scale.
SEGMENT_SCALE = UI_SCALE * (1.51 / 1.65)
