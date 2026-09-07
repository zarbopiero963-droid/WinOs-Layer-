"""Linux audio via pactl and/or PipeWire wpctl — list / volume / mute."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Callable


RunFn = Callable[..., subprocess.CompletedProcess]


def _default_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=kw.get("timeout", 5))  # noqa: S603


def audio_backend_available() -> dict[str, bool]:
    return {
        "pactl": bool(shutil.which("pactl")),
        "wpctl": bool(shutil.which("wpctl")),
    }


def list_devices(run: RunFn | None = None) -> list[dict[str, Any]]:
    run = run or _default_run
    out: list[dict[str, Any]] = []
    if shutil.which("pactl"):
        try:
            r = run(["pactl", "list", "short", "sinks"], timeout=5)
            for line in (r.stdout or "").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    out.append(
                        {
                            "id": parts[0],
                            "name": parts[1],
                            "type": "output",
                            "default": False,
                            "backend": "pactl",
                        }
                    )
            r2 = run(["pactl", "list", "short", "sources"], timeout=5)
            for line in (r2.stdout or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and not parts[1].endswith(".monitor"):
                    out.append(
                        {
                            "id": parts[0],
                            "name": parts[1],
                            "type": "input",
                            "default": False,
                            "backend": "pactl",
                        }
                    )
        except Exception:  # noqa: BLE001
            pass
    if not out and shutil.which("wpctl"):
        try:
            r = run(["wpctl", "status"], timeout=5)
            section = None
            for line in (r.stdout or "").splitlines():
                low = line.strip().lower()
                if low.startswith("audio"):
                    section = "audio"
                if "sinks:" in low:
                    section = "sinks"
                    continue
                if "sources:" in low:
                    section = "sources"
                    continue
                if section in ("sinks", "sources") and "." in line:
                    m = re.search(r"(\d+)\.\s+(.+?)(?:\s+\[|$)", line)
                    if m:
                        out.append(
                            {
                                "id": m.group(1),
                                "name": m.group(2).strip().rstrip("*").strip(),
                                "type": "output" if section == "sinks" else "input",
                                "default": "*" in line,
                                "backend": "wpctl",
                            }
                        )
        except Exception:  # noqa: BLE001
            pass
    return out


def get_volume(run: RunFn | None = None) -> dict[str, Any]:
    run = run or _default_run
    if shutil.which("pactl"):
        try:
            r = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], timeout=5)
            mute = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], timeout=5)
            vol = None
            for tok in (r.stdout or "").replace("%", " % ").split():
                if tok.isdigit():
                    vol = int(tok)
                    break
            muted = "yes" in (mute.stdout or "").lower()
            return {"volume": vol, "muted": muted, "backend": "pactl"}
        except Exception as e:  # noqa: BLE001
            return {"volume": None, "muted": None, "error": str(e), "backend": "pactl"}
    if shutil.which("wpctl"):
        try:
            r = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=5)
            # "Volume: 0.50 [MUTED]"
            text = r.stdout or ""
            m = re.search(r"([0-9.]+)", text)
            vol = int(round(float(m.group(1)) * 100)) if m else None
            muted = "[muted]" in text.lower()
            return {"volume": vol, "muted": muted, "backend": "wpctl"}
        except Exception as e:  # noqa: BLE001
            return {"volume": None, "muted": None, "error": str(e), "backend": "wpctl"}
    return {"volume": None, "muted": None, "error": "pactl/wpctl not installed"}


def set_volume(percent: int, run: RunFn | None = None) -> dict[str, Any]:
    run = run or _default_run
    percent = max(0, min(150, int(percent)))
    if shutil.which("pactl"):
        try:
            r = run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"], timeout=5)
            return {"ok": r.returncode == 0, "volume": percent, "backend": "pactl",
                    "stderr": (r.stderr or "")[:500]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "backend": "pactl"}
    if shutil.which("wpctl"):
        try:
            level = percent / 100.0
            r = run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}"], timeout=5)
            return {"ok": r.returncode == 0, "volume": percent, "backend": "wpctl",
                    "stderr": (r.stderr or "")[:500]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "backend": "wpctl"}
    return {"ok": False, "error": "pactl/wpctl not installed"}


def set_mute(muted: bool, run: RunFn | None = None) -> dict[str, Any]:
    run = run or _default_run
    if shutil.which("pactl"):
        try:
            r = run(
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0"],
                timeout=5,
            )
            return {"ok": r.returncode == 0, "muted": muted, "backend": "pactl"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "backend": "pactl"}
    if shutil.which("wpctl"):
        try:
            r = run(
                ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted else "0"],
                timeout=5,
            )
            return {"ok": r.returncode == 0, "muted": muted, "backend": "wpctl"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "backend": "wpctl"}
    return {"ok": False, "error": "pactl/wpctl not installed"}
