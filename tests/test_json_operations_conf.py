"""WebSocket json_operations paths that touch MachineStateService."""

import json
import os

import pytest

import machine_state_service as mss_mod
from domain.app_context import AppContext
from python_websocket import json_operations


def test_write_json_conf_json_invokes_set_pages_on_service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    ctx = AppContext(
        display=object(),
        assets=object(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc_calls = []

    class FakeSvc:
        def set_pages(self, pages, **kwargs):
            svc_calls.append((list(pages), dict(kwargs)))

    fake = FakeSvc()

    def fake_get():
        return fake

    monkeypatch.setattr(mss_mod, "get_machine_state_service", fake_get)
    monkeypatch.setattr(mss_mod, "_service", fake, raising=False)

    payload = {"pages": [{"uuid": "x", "widgets": []}]}
    b64 = __import__("base64").b64encode(json.dumps(payload).encode("utf-8")).decode(
        "utf-8"
    )
    result = json_operations.write_json_file("conf.json", b64)

    assert result.get("message") == "Success"
    assert len(svc_calls) == 1
    assert svc_calls[0][0] == [{"uuid": "x", "widgets": []}]


def test_get_device_info_includes_cached_pixeldarts_hardware_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "device.json").write_text(json.dumps({"model": "PixelDart"}), encoding="utf-8")

    class _RemoteSync:
        @staticmethod
        def is_connected():
            return True

    lsusb_output = (
        "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
        "Bus 001 Device 004: ID 2d80:444e PIXELDARTS\n"
    )
    calls = {"lsusb": 0}

    def fake_check_output(cmd):
        if cmd == ["lsusb"]:
            calls["lsusb"] += 1
            return lsusb_output.encode("utf-8")
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_run(*_args, **_kwargs):
        class _R:
            stdout = ""
        return _R()

    monkeypatch.setattr(json_operations, "get_remote_sync", lambda: _RemoteSync())
    monkeypatch.setattr(json_operations.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(json_operations.subprocess, "run", fake_run)

    first = json_operations.get_device_info()
    second = json_operations.get_device_info()

    assert first["device_info"]["hardware_version"] == "444e"
    assert second["device_info"]["hardware_version"] == "444e"
    assert calls["lsusb"] == 1


def test_get_device_info_uses_existing_cached_hardware_version_when_lsusb_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "device.json").write_text(json.dumps({"model": "PixelDart"}), encoding="utf-8")
    (tmp_path / ".hardware_version.json").write_text(
        json.dumps({"hardware_version": "444e"}),
        encoding="utf-8",
    )

    class _RemoteSync:
        @staticmethod
        def is_connected():
            return False

    def fake_check_output(_cmd):
        raise RuntimeError("lsusb unavailable")

    def fake_run(*_args, **_kwargs):
        class _R:
            stdout = ""
        return _R()

    monkeypatch.setattr(json_operations, "get_remote_sync", lambda: _RemoteSync())
    monkeypatch.setattr(json_operations.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(json_operations.subprocess, "run", fake_run)

    result = json_operations.get_device_info()
    assert result["device_info"]["hardware_version"] == "444e"
