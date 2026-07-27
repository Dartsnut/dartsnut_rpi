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
    payloads = iter((None, "dartsnut://device/connect?ble_name=PixelDart-eeff"))
    qr_image = object()
    sleeps = []

    monkeypatch.setattr(default_widget, "_load_device_info", lambda: {"model": "PixelDart"})
    monkeypatch.setattr(
        default_widget,
        "_desired_connection_payload",
        lambda device_info: next(payloads),
    )
    monkeypatch.setattr(default_widget, "create_qr_surface", lambda payload: qr_image)
    monkeypatch.setattr(default_widget.time, "sleep", lambda seconds: sleeps.append(seconds))

    payload = default_widget._display_connection_qr(display)

    assert payload == "dartsnut://device/connect?ble_name=PixelDart-eeff"
    assert sleeps == [1]
    assert display.frames == [qr_image]


def test_default_widget_desired_payload_switches_to_bind(monkeypatch):
    monkeypatch.setattr(
        default_widget, "resolve_bluetooth_local_name", lambda _info: "PixelDart-eeff"
    )
    monkeypatch.setattr(
        default_widget,
        "read_qr_status",
        lambda: {"supabase_connected": True, "device_id": "AA:BB"},
    )

    assert default_widget._desired_connection_payload({}) == (
        "dartsnut://device/bind?device_id=AA%3ABB"
    )


def test_default_widget_refresh_loop_switches_qr_both_directions(monkeypatch):
    display = _Display()
    payloads = iter(
        (
            "dartsnut://device/bind?device_id=DEVICE",
            "dartsnut://device/connect?ble_name=PixelDart-eeff",
        )
    )
    sleeps = {"count": 0}

    def fake_sleep(_seconds):
        sleeps["count"] += 1
        if sleeps["count"] > 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(default_widget.time, "sleep", fake_sleep)
    monkeypatch.setattr(default_widget, "_load_device_info", lambda: {})
    monkeypatch.setattr(
        default_widget,
        "_desired_connection_payload",
        lambda _device_info: next(payloads),
    )
    monkeypatch.setattr(default_widget, "create_qr_surface", lambda payload: payload)

    try:
        default_widget._refresh_connection_qr_loop(
            display, "dartsnut://device/connect?ble_name=PixelDart-eeff"
        )
    except KeyboardInterrupt:
        pass

    assert display.frames == [
        "dartsnut://device/bind?device_id=DEVICE",
        "dartsnut://device/connect?ble_name=PixelDart-eeff",
    ]
