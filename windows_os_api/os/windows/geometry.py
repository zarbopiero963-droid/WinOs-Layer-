"""Validation for window move/resize requests — one home, called at every backend.

Same shape as `os/terminal/allowlist.py`: the rule lives in one module and each
backend calls it at the point that actually acts, so a caller that reaches a
backend directly cannot skip it and the three implementations cannot drift.

The limits are not arbitrary. X11 window geometry is signed 16-bit, and the Win32
`RECT` fields are `LONG` but window managers on both platforms behave erratically
well before that. Anything outside the X11 range is refused on every platform, so
the same request gets the same answer wherever it lands.
"""
from __future__ import annotations

# X11 stores window coordinates in int16 and dimensions in uint16.
COORD_MIN = -32768
COORD_MAX = 32767
SIZE_MIN = 1
SIZE_MAX = 32767


class GeometryRejected(Exception):
    """The requested geometry is not usable. Carries the reason shown to the caller."""


def _as_int(value: object, field: str) -> int:
    """Accept only a real integer.

    `bool` is a subclass of `int` in Python, so `True` would otherwise sail
    through as `1` — a silent reinterpretation of the caller's request.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeometryRejected(f"{field} must be an integer, got {type(value).__name__}")
    return value


def validate_position(x: object, y: object) -> tuple[int, int]:
    """Coordinates for a move. Negative is legitimate — a window may sit off-screen."""
    xi, yi = _as_int(x, "x"), _as_int(y, "y")
    for name, val in (("x", xi), ("y", yi)):
        if not COORD_MIN <= val <= COORD_MAX:
            raise GeometryRejected(
                f"{name}={val} out of range ({COORD_MIN}..{COORD_MAX})"
            )
    return xi, yi


def validate_size(width: object, height: object) -> tuple[int, int]:
    """Dimensions for a resize. Zero and negative are refused, not clamped."""
    wi, hi = _as_int(width, "width"), _as_int(height, "height")
    for name, val in (("width", wi), ("height", hi)):
        if not SIZE_MIN <= val <= SIZE_MAX:
            raise GeometryRejected(
                f"{name}={val} out of range ({SIZE_MIN}..{SIZE_MAX})"
            )
    return wi, hi
