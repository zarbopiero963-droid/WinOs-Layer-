"""Linux display-session detection + Wayland/X11 command construction.

Honest limits: Wayland input/window control depends on compositor tools
(ydotool/wtype/dotool, wlrctl/swaymsg/hyprctl). Portal-based capture is
not implemented; capability_flags reflect what is actually present.
"""
from __future__ import annotations

import os
import shutil
from typing import Any


def detect_session(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Detect X11 vs Wayland vs unknown from environment."""
    e = env if env is not None else dict(os.environ)
    session_type = (e.get("XDG_SESSION_TYPE") or "").strip().lower()
    wayland_display = (e.get("WAYLAND_DISPLAY") or "").strip()
    display = (e.get("DISPLAY") or "").strip()
    xdg_desktop = (e.get("XDG_CURRENT_DESKTOP") or e.get("DESKTOP_SESSION") or "").strip()

    if not session_type:
        if wayland_display:
            session_type = "wayland"
        elif display:
            session_type = "x11"
        else:
            session_type = "unknown"

    is_wayland = session_type == "wayland" or bool(wayland_display)
    is_x11 = session_type == "x11" or (bool(display) and not is_wayland)

    compositor = _guess_compositor(e, xdg_desktop)
    return {
        "session_type": session_type,
        "wayland": is_wayland,
        "x11": is_x11,
        "WAYLAND_DISPLAY": wayland_display or None,
        "DISPLAY": display or None,
        "desktop": xdg_desktop or None,
        "compositor": compositor,
    }


def _guess_compositor(env: dict[str, str], desktop: str) -> str | None:
    desk = desktop.lower()
    if "hypr" in desk or env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if "sway" in desk or env.get("SWAYSOCK"):
        return "sway"
    if "gnome" in desk:
        return "gnome"
    if "kde" in desk or "plasma" in desk:
        return "kde"
    if "wlroots" in desk or env.get("WLR_BACKENDS"):
        return "wlroots"
    return None


def wayland_input_tool() -> str | None:
    """Prefer ydotool → wtype → dotool when present."""
    for tool in ("ydotool", "wtype", "dotool"):
        if shutil.which(tool):
            return tool
    return None


def wayland_window_tool() -> str | None:
    for tool in ("wlrctl", "swaymsg", "hyprctl"):
        if shutil.which(tool):
            return tool
    if shutil.which("wayland-info"):
        return "wayland-info"
    return None


def build_wayland_type_cmd(text: str, tool: str | None = None) -> list[str] | None:
    """Construct argv to type text on Wayland (no execution)."""
    tool = tool or wayland_input_tool()
    if not tool:
        return None
    if tool == "ydotool":
        return ["ydotool", "type", "--", text]
    if tool == "wtype":
        return ["wtype", "--", text]
    if tool == "dotool":
        # dotool reads commands from stdin; represent as argv+payload hint
        return ["dotool"]
    return None


def build_wayland_click_cmd(
    x: int, y: int, button: str = "left", tool: str | None = None
) -> list[str] | None:
    tool = tool or wayland_input_tool()
    if not tool:
        return None
    btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
    if tool == "ydotool":
        return ["ydotool", "mousemove", str(x), str(y), "click", btn]
    # wtype cannot click; document limitation
    if tool == "wtype":
        return None
    if tool == "dotool":
        return ["dotool"]
    return None


def build_wayland_key_cmd(
    key: str, modifiers: list[str] | None = None, tool: str | None = None
) -> list[str] | None:
    tool = tool or wayland_input_tool()
    if not tool:
        return None
    mods = modifiers or []
    if tool == "ydotool":
        seq = "+".join([*mods, key]) if mods else key
        return ["ydotool", "key", seq]
    if tool == "wtype":
        args = ["wtype"]
        for m in mods:
            args.extend(["-M", m])
        args.extend(["-k", key])
        return args
    return None


def build_wayland_list_windows_cmd(tool: str | None = None) -> list[str] | None:
    tool = tool or wayland_window_tool()
    if not tool:
        return None
    if tool == "wlrctl":
        return ["wlrctl", "toplevel", "list"]
    if tool == "swaymsg":
        return ["swaymsg", "-t", "get_tree"]
    if tool == "hyprctl":
        return ["hyprctl", "clients", "-j"]
    if tool == "wayland-info":
        return ["wayland-info"]
    return None


def probe_wayland_capabilities(env: dict[str, str] | None = None) -> dict[str, Any]:
    session = detect_session(env)
    input_tool = wayland_input_tool()
    window_tool = wayland_window_tool()
    windows_ui = bool(
        (session["x11"] and (shutil.which("wmctrl") or shutil.which("xdotool")))
        or (session["wayland"] and window_tool and window_tool != "wayland-info")
        or (session["wayland"] and input_tool)
    )
    return {
        **session,
        "wayland_input_tool": input_tool,
        "wayland_window_tool": window_tool,
        "windows_ui": windows_ui,
        "notes": {
            "portal": "xdg-desktop-portal screencast/input not implemented",
            "compositor_variance": "window list/control is compositor-specific",
        },
    }
