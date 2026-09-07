"""OCR / vision fallback for apps without AT-SPI (canvas, games, custom UI)."""
from __future__ import annotations

from windows_os_api.apps.vision.ocr import (
    click_text,
    find_text_on_image,
    find_text_on_screen,
    render_text_fixture,
    VisionBox,
)

__all__ = [
    "VisionBox",
    "click_text",
    "find_text_on_image",
    "find_text_on_screen",
    "render_text_fixture",
]
