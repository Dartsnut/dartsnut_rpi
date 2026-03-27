import sys
import types

import runtime.machine_api as machine_api


def test_user_data_wrappers_delegate(monkeypatch):
    calls = []
    fake_mod = types.SimpleNamespace(
        stop_game_tracking=lambda: calls.append("stop"),
        reset_user_data_file=lambda: calls.append("reset"),
    )
    monkeypatch.setitem(sys.modules, "python_websocket.user_data_operations", fake_mod)

    machine_api.stop_game_tracking()
    machine_api.reset_user_data_file()

    assert calls == ["stop", "reset"]


def test_device_wrappers_delegate(monkeypatch):
    calls = []
    fake_mod = types.SimpleNamespace(
        _parse_hhmm=lambda s: ("p", s),
        forget_wifi=lambda: calls.append("forget"),
    )
    monkeypatch.setitem(sys.modules, "python_websocket.device_operations", fake_mod)

    assert machine_api.parse_hhmm("23:59") == ("p", "23:59")
    machine_api.forget_wifi()
    assert calls == ["forget"]


def test_git_wrappers_delegate(monkeypatch):
    fake_mod = types.SimpleNamespace(
        get_version=lambda: {"version": "1.2.3"},
        perform_update=lambda: {"error": False},
    )
    monkeypatch.setitem(sys.modules, "python_websocket.git_operations", fake_mod)

    assert machine_api.get_version() == {"version": "1.2.3"}
    assert machine_api.perform_update() == {"error": False}


def test_udp_wrappers_delegate(monkeypatch):
    fake_mod = types.SimpleNamespace(
        get_ip_address=lambda: "192.168.0.2",
        get_current_ssid=lambda: "wifi",
        normalize_ip=lambda v: f"ip:{v}",
        normalize_ssid=lambda v: f"ssid:{v}",
    )
    monkeypatch.setitem(sys.modules, "python_websocket.udp_broadcast", fake_mod)

    assert machine_api.get_ip_address() == "192.168.0.2"
    assert machine_api.get_current_ssid() == "wifi"
    assert machine_api.normalize_ip("1.1.1.1") == "ip:1.1.1.1"
    assert machine_api.normalize_ssid("abc") == "ssid:abc"


def test_bluetooth_wrappers_delegate(monkeypatch):
    fake_mod = types.SimpleNamespace(
        build_firestore_bluetooth_list=lambda: [{"address": "aa"}],
        connect_device_for_firestore=lambda a: {"ok": a},
        current_utc_iso_timestamp=lambda: "2026-03-26T00:00:00+00:00",
    )
    monkeypatch.setitem(sys.modules, "python_websocket.bluetooth_operations", fake_mod)

    assert machine_api.build_firestore_bluetooth_list() == [{"address": "aa"}]
    assert machine_api.connect_device_for_firestore("AA:BB") == {"ok": "AA:BB"}
    assert machine_api.current_utc_iso_timestamp() == "2026-03-26T00:00:00+00:00"
    assert machine_api.build_remote_bluetooth_list() == [{"address": "aa"}]
    assert machine_api.connect_device_for_remote("AA:BB") == {"ok": "AA:BB"}
