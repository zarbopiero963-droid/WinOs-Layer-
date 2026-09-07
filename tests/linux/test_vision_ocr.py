"""Hard tests for vision/OCR fallback — no tesseract required."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux vision tests", allow_module_level=True)


def test_render_and_find_text_template():
    from windows_os_api.apps.vision.ocr import find_text_on_image, render_text_fixture

    img = render_text_fixture("HelloWinOS", width=500, height=140, font_size=40, x=30, y=40)
    boxes = find_text_on_image(img, "HelloWinOS", prefer_tesseract=False)
    assert boxes, "template matcher must find rendered text"
    b = boxes[0]
    assert b["width"] > 10 and b["height"] > 5
    assert b["confidence"] >= 0.35
    assert b["engine"] == "template"
    # Center should be somewhere in the left/upper region of the fixture
    assert 0 <= b["left"] < 400
    assert 0 <= b["top"] < 120


def test_find_text_on_screen_with_image():
    from windows_os_api.apps.vision.ocr import find_text_on_screen, render_text_fixture

    img = render_text_fixture("ClickMe", width=320, height=100, font_size=36)
    result = find_text_on_screen("ClickMe", image=img)
    assert result["ok"] is True
    assert result["boxes"]
    assert result["source"] == "image"


def test_click_text_dry_run():
    from windows_os_api.apps.vision.ocr import click_text, render_text_fixture

    img = render_text_fixture("OKButton", width=360, height=110, font_size=36)
    clicks = []

    def fake_click(x, y):
        clicks.append((x, y))
        return {"ok": True, "x": x, "y": y}

    dry = click_text("OKButton", dry_run=True, image=img, click_fn=fake_click)
    assert dry["ok"] is True
    assert dry["dry_run"] is True
    assert not clicks

    live = click_text("OKButton", dry_run=False, image=img, click_fn=fake_click)
    assert live["ok"] is True
    assert clicks
    assert live["x"] == clicks[0][0]


def test_missing_text_returns_empty():
    from windows_os_api.apps.vision.ocr import find_text_on_image, render_text_fixture

    img = render_text_fixture("Alpha", width=300, height=100)
    boxes = find_text_on_image(img, "ZZZNotPresentXYZ", prefer_tesseract=False)
    assert boxes == []


@pytest.mark.skipif(
    __import__("shutil").which("tesseract") is None,
    reason="tesseract not installed",
)
def test_tesseract_live_optional():
    from windows_os_api.apps.vision import ocr

    if not ocr.tesseract_available():
        pytest.skip("pytesseract missing")
    img = ocr.render_text_fixture("TessOK", width=400, height=120, font_size=40)
    boxes = ocr.find_text_on_image(img, "TessOK", prefer_tesseract=True)
    assert boxes


def test_linux_capability_flags_include_ocr_vision(linux_backend):
    caps = linux_backend.capability_flags()
    assert caps.get("ocr") is True
    assert caps.get("vision") is True
    assert "ocr_tesseract" in caps
    assert "wayland" in caps
    assert "privileged" in caps
    assert caps.get("privileged") is False
