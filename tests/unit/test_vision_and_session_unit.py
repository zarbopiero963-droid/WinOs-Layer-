"""Portable unit tests (fake backend) for vision helpers + loginctl parse."""
from __future__ import annotations

from windows_os_api.apps.vision.ocr import find_text_on_image, render_text_fixture, click_text
from windows_os_api.core.security.privilege import parse_loginctl_sessions, deny_structured


def test_fixture_roundtrip_unit():
    img = render_text_fixture("UnitOCR", width=420, height=120, font_size=40)
    boxes = find_text_on_image(img, "UnitOCR", prefer_tesseract=False)
    assert boxes and boxes[0]["engine"] == "template"


def test_click_dry_run_unit():
    img = render_text_fixture("Go", width=200, height=80, font_size=40)
    r = click_text("Go", dry_run=True, image=img, click_fn=lambda x, y: {"ok": True})
    assert r["ok"] and r["dry_run"]


def test_parse_loginctl():
    text = "3 1000 box seat0 tty2\n5 1001 other seat0\n"
    rows = parse_loginctl_sessions(text)
    assert len(rows) == 2
    assert rows[0]["user"] == "box"
    assert rows[0]["seat"] == "seat0"


def test_deny_structured():
    d = deny_structured("nope", code="x")
    assert d["ok"] is False and d["denied"] is True and d["code"] == "x"
