import json

import supabase_sync_bridge as ssb


# Initial-state construction (happy path -> fallback identity)
def test_build_initial_state_includes_remote_parity_fields(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)

    state = ssb._build_initial_state(
        {
            "id": "AA:BB:CC:DD:EE:FF",
            "brightness": "70",
            "volume": "50",
            "dim_window_enabled": True,
            "dim_window_start": "22:00",
            "dim_window_end": "06:00",
            "dim_level": 20,
            "dim_restore_seconds": 30,
            "serial": "SN123",
            "model": "PixelBoard",
            "name": "Kitchen",
            "firmware_version": "1.2.3",
            "firmware_update": False,
            "updated_at": "2026-01-01T00:00:00",
        }
    )

    assert state["device_info"]["id"] == "AA:BB:CC:DD:EE:FF"
    assert state["brightness"] == 70
    assert state["volume"] == 50
    assert state["pages"] == []
    assert state["games"] == []
    assert state["dim_window"]["dim_window_enabled"] is True
    assert state["device_info"] == {
        "id": "AA:BB:CC:DD:EE:FF",
        "sn": "SN123",
        "model": "PixelBoard",
        "name": "Kitchen",
    }
    assert state["firmware"] == {"version": "1.2.3", "update": False}


def test_build_initial_state_sets_device_info_id_from_ble_mac_when_missing(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)

    state = ssb._build_initial_state(
        {
            "ble_mac": "aa:bb:cc:dd:ee:ff",
            "brightness": "70",
            "volume": "50",
        }
    )

    assert state["device_info"]["id"] == "AA:BB:CC:DD:EE:FF"
    assert state["device_info"]["id"] == "AA:BB:CC:DD:EE:FF"


# Payload normalization and merge guards
def test_coerce_pages_games_lists():
    out = ssb._coerce_pages_games_lists({"pages": None, "games": "bad"})
    assert out == {"pages": [], "games": []}


def test_merge_remote_and_local_preserves_device_id(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(ssb, "_build_initial_state", lambda _d: {"device_info": {"id": "AA:BB:CC:DD:EE:FF"}})
    merged = ssb._merge_remote_and_local({"pages": None, "games": None})
    assert merged["device_info"]["id"] == "AA:BB:CC:DD:EE:FF"
    assert merged["pages"] == []
    assert merged["games"] == []



def test_load_device_json_reads_device_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(tmp_path / "device.json", "w", encoding="utf-8") as f:
        json.dump({"id": "11:22:33:44:55:66", "name": "Lab"}, f)
    assert ssb._load_device_json() == {"id": "11:22:33:44:55:66", "name": "Lab"}


def test_request_device_reset_state_includes_device_info_from_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ssb, "_resolve_hardware_version", lambda: "")
    with open(tmp_path / "device.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "id": "AA:BB:CC:DD:EE:FF",
                "serial": "S1",
                "model": "PixelDart",
                "name": "Den",
            },
            f,
        )
    captured = []
    monkeypatch.setattr(ssb, "publish_device_state_update", lambda p: captured.append(p))
    ssb.request_device_reset_state()
    assert len(captured) == 1
    assert captured[0]["device_info"] == {
        "id": "AA:BB:CC:DD:EE:FF",
        "sn": "S1",
        "model": "PixelDart",
        "name": "Den",
    }


def test_build_initial_state_adds_hardware_version_when_missing(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(ssb, "_resolve_hardware_version", lambda: "444e")

    state = ssb._build_initial_state(
        {
            "id": "AA:BB:CC:DD:EE:FF",
            "brightness": "70",
            "volume": "50",
            "serial": "SN123",
            "model": "PixelBoard",
            "name": "Kitchen",
        }
    )

    assert state["device_info"]["hardware_version"] == "444e"
