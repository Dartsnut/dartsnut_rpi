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


def test_request_device_reset_state_sends_expected_payload(monkeypatch):
    captured = []
    monkeypatch.setattr(rsb._ssb, "publish_device_state_update", lambda payload: captured.append(payload))

    rsb.request_device_reset_state()

    assert captured == [
        {
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    ]
