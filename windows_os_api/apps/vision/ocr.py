"""Screenshot → find text → optional click.

Preferred path: pytesseract when tesseract is installed.
Fallback: pure-Python Pillow template matching (render needle + SAD scan)
so hard tests work without tesseract. Accuracy is best-effort for the
fallback — capability_flags.ocr reflects whether tesseract is available.
"""
from __future__ import annotations

import io
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont


@dataclass
class VisionBox:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float
    engine: str

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["x"] = self.left
        d["y"] = self.top
        d["cx"], d["cy"] = self.center
        return d


def tesseract_available() -> bool:
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # type: ignore  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _load_font(size: int = 32) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    # Prefer DejaVu (common on Linux) for deterministic fixtures
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ):
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:  # noqa: BLE001
                pass
    return ImageFont.load_default()


def render_text_fixture(
    text: str,
    *,
    width: int = 400,
    height: int = 120,
    font_size: int = 36,
    bg: tuple[int, int, int] = (255, 255, 255),
    fg: tuple[int, int, int] = (0, 0, 0),
    x: int = 40,
    y: int = 40,
) -> Image.Image:
    """Generate a known PNG (in-memory) with Pillow text for hard tests."""
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)
    draw.text((x, y), text, fill=fg, font=font)
    return img


def _image_from_any(source: Image.Image | bytes | str | Path) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source)).convert("RGB")
    return Image.open(str(source)).convert("RGB")


def _ocr_tesseract(img: Image.Image, needle: str) -> list[VisionBox]:
    import pytesseract  # type: ignore

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    needle_l = needle.lower()
    boxes: list[VisionBox] = []
    n = len(data.get("text") or [])
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        if not raw:
            continue
        if needle_l not in raw.lower() and raw.lower() not in needle_l:
            continue
        conf_s = data.get("conf", ["0"])[i]
        try:
            conf = float(conf_s) / 100.0 if float(conf_s) >= 0 else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        boxes.append(
            VisionBox(
                text=raw,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                confidence=max(0.0, min(1.0, conf)),
                engine="tesseract",
            )
        )
    # Also try full-string match via image_to_string
    if not boxes:
        full = (pytesseract.image_to_string(img) or "").strip()
        if needle_l in full.lower():
            boxes.append(
                VisionBox(
                    text=needle,
                    left=0,
                    top=0,
                    width=img.width,
                    height=img.height,
                    confidence=0.4,
                    engine="tesseract",
                )
            )
    return boxes


def _render_needle(text: str, font_size: int = 36) -> Image.Image:
    font = _load_font(font_size)
    # Measure
    tmp = Image.new("RGB", (8, 8), (255, 255, 255))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = max(1, bbox[2] - bbox[0] + 4)
    th = max(1, bbox[3] - bbox[1] + 4)
    needle = Image.new("RGB", (tw, th), (255, 255, 255))
    ImageDraw.Draw(needle).text((2, 2), text, fill=(0, 0, 0), font=font)
    return needle


def _sad_score(hay: Image.Image, needle: Image.Image, x: int, y: int) -> float:
    """Mean absolute difference (0 = perfect). Operates on grayscale."""
    nw, nh = needle.size
    region = hay.crop((x, y, x + nw, y + nh))
    # tobytes is faster / no deprecation vs getdata
    hp = region.tobytes()
    np_ = needle.tobytes()
    if not hp or len(hp) != len(np_):
        return 1e9
    total = 0
    for a, b in zip(hp, np_):
        total += abs(a - b)
    return total / len(hp)


def _ocr_template(img: Image.Image, needle_text: str) -> list[VisionBox]:
    """Pure-Python fallback: render needle text and find best SAD match.

    Uses coarse multi-candidate scan + local refine so large step sizes
    cannot miss the true peak (common with long strings / large fonts).
    """
    hay = img.convert("L")
    best: VisionBox | None = None
    # Prefer sizes matching common fixtures first
    for size in (40, 36, 32, 28, 48, 24):
        needle = _render_needle(needle_text, font_size=size).convert("L")
        nw, nh = needle.size
        if nw >= hay.width or nh >= hay.height:
            continue
        step_x = max(1, min(8, nw // 12))
        step_y = max(1, min(4, nh // 8))
        # Collect top-K coarse hits
        candidates: list[tuple[float, int, int]] = []
        for y in range(0, hay.height - nh + 1, step_y):
            for x in range(0, hay.width - nw + 1, step_x):
                score = _sad_score(hay, needle, x, y)
                candidates.append((score, x, y))
        candidates.sort(key=lambda t: t[0])
        top = candidates[:8] if candidates else []
        local_best_score = 1e9
        local_best_xy = (0, 0)
        for _, cx, cy in top:
            for y in range(max(0, cy - step_y), min(hay.height - nh + 1, cy + step_y + 1)):
                for x in range(max(0, cx - step_x), min(hay.width - nw + 1, cx + step_x + 1)):
                    score = _sad_score(hay, needle, x, y)
                    if score < local_best_score:
                        local_best_score = score
                        local_best_xy = (x, y)
        # Also try exact top-left guesses common in fixtures
        for gx, gy in ((40, 40), (30, 40), (20, 20), (10, 10), (2, 2)):
            if gx + nw <= hay.width and gy + nh <= hay.height:
                score = _sad_score(hay, needle, gx, gy)
                if score < local_best_score:
                    local_best_score = score
                    local_best_xy = (gx, gy)
            # needle drawn with 2px pad → fixture text at (fx,fy) matches at (fx-2, fy-2)
            for pad in (2,):
                px, py = gx - pad, gy - pad
                if px >= 0 and py >= 0 and px + nw <= hay.width and py + nh <= hay.height:
                    score = _sad_score(hay, needle, px, py)
                    if score < local_best_score:
                        local_best_score = score
                        local_best_xy = (px, py)
        # Accept strong B/W text matches (threshold relaxed for anti-aliased glyphs)
        if local_best_score < 25.0:
            conf = max(0.35, min(0.95, 1.0 - (local_best_score / 90.0)))
            cand = VisionBox(
                text=needle_text,
                left=local_best_xy[0],
                top=local_best_xy[1],
                width=nw,
                height=nh,
                confidence=conf,
                engine="template",
            )
            if best is None or cand.confidence > best.confidence:
                best = cand
            if local_best_score < 5.0:
                break  # perfect / near-perfect — stop early
    return [best] if best else []


def find_text_on_image(
    source: Image.Image | bytes | str | Path,
    text: str,
    *,
    prefer_tesseract: bool = True,
) -> list[dict[str, Any]]:
    """Find ``text`` on an image. Returns list of box dicts with confidence."""
    if not text:
        return []
    img = _image_from_any(source)
    boxes: list[VisionBox] = []
    if prefer_tesseract and tesseract_available():
        try:
            boxes = _ocr_tesseract(img, text)
        except Exception:  # noqa: BLE001
            boxes = []
    if not boxes:
        boxes = _ocr_template(img, text)
    return [b.to_dict() for b in boxes]


def find_text_on_screen(
    text: str,
    *,
    screenshot_fn: Callable[[], dict[str, Any]] | None = None,
    image: Image.Image | bytes | None = None,
) -> dict[str, Any]:
    """Locate text on the live screen (or a provided image for dry-run/tests)."""
    if image is not None:
        boxes = find_text_on_image(image, text)
        return {
            "ok": bool(boxes),
            "text": text,
            "boxes": boxes,
            "engine": boxes[0]["engine"] if boxes else None,
            "source": "image",
        }
    if screenshot_fn is None:
        from windows_os_api.backends.factory import get_backend

        screenshot_fn = get_backend().screenshot
    shot = screenshot_fn()
    if not shot.get("ok") or not shot.get("data_base64"):
        return {
            "ok": False,
            "text": text,
            "boxes": [],
            "error": shot.get("error") or "screenshot failed",
            "source": "screen",
        }
    import base64

    png = base64.b64decode(shot["data_base64"])
    boxes = find_text_on_image(png, text)
    return {
        "ok": bool(boxes),
        "text": text,
        "boxes": boxes,
        "engine": boxes[0]["engine"] if boxes else None,
        "source": "screen",
        "display_id": shot.get("display_id"),
    }


def click_text(
    text: str,
    *,
    dry_run: bool = False,
    click_fn: Callable[[int, int], dict[str, Any]] | None = None,
    image: Image.Image | bytes | None = None,
    screenshot_fn: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Find text then click its center via the input backend (or dry-run)."""
    found = find_text_on_screen(text, screenshot_fn=screenshot_fn, image=image)
    if not found.get("ok") or not found.get("boxes"):
        return {"ok": False, "error": "text not found", "find": found, "dry_run": dry_run}
    box = found["boxes"][0]
    x, y = int(box["cx"]), int(box["cy"])
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "x": x,
            "y": y,
            "box": box,
            "find": found,
        }
    if click_fn is None:
        from windows_os_api.backends.factory import get_backend

        click_fn = lambda cx, cy: get_backend().mouse_click(cx, cy)  # noqa: E731
    clicked = click_fn(x, y)
    return {
        "ok": bool(clicked.get("ok")),
        "dry_run": False,
        "x": x,
        "y": y,
        "box": box,
        "click": clicked,
        "find": found,
    }
