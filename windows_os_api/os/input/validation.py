"""Validation for mouse/keyboard requests — one home, called at every backend.

Same structure as `os/windows/geometry.py` and `os/terminal/allowlist.py`: the
rule lives in one module and each backend applies it at the point that acts, so
a caller reaching a backend directly gets the same answer as one coming through
the REST layer, and the three implementations cannot drift.

Why a validator rather than a `dict.get(name, default)`
-------------------------------------------------------
The mapping this replaces was::

    btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")

A typo did not fail — it fell through to the default. `mouse_click(x, y,
"rihgt")` performed a **left** click and returned ``{"ok": True, "button":
"rihgt"}``: the name the caller asked for, echoed back, next to an action that
was something else. An unknown button is now refused, because reporting an
action you did not take is worse than refusing one you cannot.
"""
from __future__ import annotations

# X11 button numbers. 4/5 are the vertical wheel, 6/7 the horizontal one —
# scrolling on X is a button press, which is why direction maps here too.
MOUSE_BUTTONS: dict[str, int] = {"left": 1, "middle": 2, "right": 3}
SCROLL_BUTTONS: dict[str, int] = {"up": 4, "down": 5, "left": 6, "right": 7}

# A wheel notch is one button press; the tool repeats it. The cap keeps a typo
# ("amount": 100000) from locking the display up in a loop nobody asked for.
SCROLL_MIN = 1
SCROLL_MAX = 100

# Enough for a chord like ctrl+shift+alt+super+key, not enough to be a payload.
MAX_HOTKEY_KEYS = 8
MAX_KEY_NAME = 32

DRAG_STEPS_MIN = 1
DRAG_STEPS_MAX = 200


class InputRejected(Exception):
    """The request is not usable. Carries the reason shown to the caller."""


def validate_button(button: object) -> str:
    """A mouse button name, refused rather than defaulted when unknown."""
    if not isinstance(button, str):
        raise InputRejected(f"button must be a string, got {type(button).__name__}")
    name = button.lower()
    if name not in MOUSE_BUTTONS:
        raise InputRejected(
            f"unknown mouse button {button!r}. Permitted: {', '.join(sorted(MOUSE_BUTTONS))}"
        )
    return name


def validate_scroll(direction: object, amount: object) -> tuple[str, int]:
    if not isinstance(direction, str):
        raise InputRejected(f"direction must be a string, got {type(direction).__name__}")
    name = direction.lower()
    if name not in SCROLL_BUTTONS:
        raise InputRejected(
            f"unknown scroll direction {direction!r}. "
            f"Permitted: {', '.join(sorted(SCROLL_BUTTONS))}"
        )
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise InputRejected(f"amount must be an integer, got {type(amount).__name__}")
    if not SCROLL_MIN <= amount <= SCROLL_MAX:
        raise InputRejected(f"amount={amount} out of range ({SCROLL_MIN}..{SCROLL_MAX})")
    return name, amount


def validate_key(key: object) -> str:
    """A single key name, passed to the OS as one argv element.

    There is no shell here, so this is not injection defence — it is a refusal
    to forward something that cannot be a key name, so the caller is told what
    is wrong instead of getting an opaque failure from the tool.
    """
    if not isinstance(key, str):
        raise InputRejected(f"key must be a string, got {type(key).__name__}")
    name = key.strip()
    if not name:
        raise InputRejected("key must not be empty")
    if len(name) > MAX_KEY_NAME:
        raise InputRejected(f"key name too long ({len(name)} > {MAX_KEY_NAME})")
    if any(c.isspace() for c in name):
        raise InputRejected(f"key {key!r} must not contain whitespace")
    return name


def validate_hotkey(keys: object) -> list[str]:
    """A chord: several keys pressed together, in order, then released."""
    if isinstance(keys, str):
        raise InputRejected("hotkey takes a list of keys, not a single string")
    if not isinstance(keys, (list, tuple)):
        raise InputRejected(f"keys must be a list, got {type(keys).__name__}")
    if not keys:
        raise InputRejected("hotkey needs at least one key")
    if len(keys) > MAX_HOTKEY_KEYS:
        raise InputRejected(f"too many keys in chord ({len(keys)} > {MAX_HOTKEY_KEYS})")
    return [validate_key(k) for k in keys]


def validate_steps(steps: object) -> int:
    """Intermediate positions in a drag.

    A drag is not a teleport: an application that tracks the pointer needs to
    see it move, so the path is sent as several moves rather than one jump.
    """
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise InputRejected(f"steps must be an integer, got {type(steps).__name__}")
    if not DRAG_STEPS_MIN <= steps <= DRAG_STEPS_MAX:
        raise InputRejected(f"steps={steps} out of range ({DRAG_STEPS_MIN}..{DRAG_STEPS_MAX})")
    return steps
