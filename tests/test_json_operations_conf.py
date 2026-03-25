"""WebSocket json_operations paths that touch MachineStateService."""

import json
import os

import pytest

import machine_state_service as mss_mod
from app_context import AppContext
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
