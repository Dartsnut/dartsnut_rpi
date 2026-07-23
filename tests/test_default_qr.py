from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

sys.modules.setdefault("pydartsnut", SimpleNamespace(Dartsnut=object))
default_widget = importlib.import_module("default")


class _Display:
    def __init__(self):
        self.frames = []

    def update_frame_buffer(self, image):
        self.frames.append(image)


def test_default_widget_retries_then_displays_dynamic_qr(monkeypatch):
    display = _Display()
    qr_image = object()
    results = iter((None, qr_image))
    sleeps = []

    monkeypatch.setattr(default_widget, "_load_device_info", lambda: {"model": "PixelDart"})
    monkeypatch.setattr(
        default_widget,
        "create_bluetooth_qr_for_device",
        lambda device_info: next(results),
    )
    monkeypatch.setattr(default_widget.time, "sleep", lambda seconds: sleeps.append(seconds))

    default_widget._display_connection_qr(display)

    assert sleeps == [1]
    assert display.frames == [qr_image]
