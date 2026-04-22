import json

import remote_sync_bridge as rsb


def test_normalize_config_payload_converts_null_lists_to_empty_lists():
    payload = {
        "ip_address": "",
        "ssid": "",
        "pages": None,
        "games": None,
        "dim_window": {"dim_window_enabled": False},
    }

    normalized = rsb._normalize_config_payload(payload)

    assert normalized["pages"] == []
    assert normalized["games"] == []
    assert normalized["ip_address"] == ""


def test_build_initial_state_includes_remote_parity_fields(monkeypatch):
    monkeypatch.setattr(rsb._ssb.os.path, "isfile", lambda _p: False)

    state = rsb._build_initial_state(
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


def test_request_device_reset_state_sends_expected_payload(monkeypatch, tmp_path):
    captured = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rsb._ssb, "_resolve_hardware_version", lambda: "")
    with open(tmp_path / "device.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "id": "AA:BB:CC:DD:EE:FF",
                "serial": "SN-RESET",
                "model": "PixelBoard",
                "name": "Kitchen",
                "brightness": "40",
                "volume": "30",
            },
            f,
        )
    monkeypatch.setattr(rsb._ssb, "publish_device_state_update", lambda payload: captured.append(payload))

    rsb.request_device_reset_state()

    assert len(captured) == 1
    payload = captured[0]
    assert payload["ip_address"] == ""
    assert payload["ssid"] == ""
    assert payload["pages"] == []
    assert payload["games"] == []
    assert payload["brightness"] == 100
    assert payload["volume"] == 100
    assert payload["dim_window"] == {
        "dim_window_enabled": False,
        "dim_window_start": "22:00",
        "dim_window_end": "8:00",
        "dim_level": 10,
        "dim_restore_seconds": 5,
    }
    assert payload["device_info"] == {
        "id": "AA:BB:CC:DD:EE:FF",
        "sn": "SN-RESET",
        "model": "PixelBoard",
        "name": "Kitchen",
    }
    assert isinstance(payload.get("pages_updated_at"), str)
    assert payload["pages_updated_at"] != ""
    assert isinstance(payload.get("device_updated_at"), str)
    assert payload["device_updated_at"] != ""
