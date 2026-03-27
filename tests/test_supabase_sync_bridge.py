import supabase_sync_bridge as ssb


def test_build_initial_state_includes_firestore_parity_fields(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)

    state = ssb._build_initial_state(
        {
            "device_id": "AA:BB:CC:DD:EE:FF",
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

    assert state["device_id"] == "AA:BB:CC:DD:EE:FF"
    assert state["brightness"] == 70
    assert state["volume"] == 50
    assert state["pages"] == []
    assert state["games"] == []
    assert state["dim_window"]["dim_window_enabled"] is True
    assert state["device_info"] == {
        "sn": "SN123",
        "model": "PixelBoard",
        "name": "Kitchen",
    }
    assert state["firmware"] == {"version": "1.2.3", "update": False}


def test_coerce_pages_games_lists():
    out = ssb._coerce_pages_games_lists({"pages": None, "games": "bad"})
    assert out == {"pages": [], "games": []}


def test_merge_remote_and_local_preserves_device_id(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(ssb, "_build_initial_state", lambda _d: {"device_id": "AA:BB:CC:DD:EE:FF"})
    merged = ssb._merge_remote_and_local({"pages": None, "games": None})
    assert merged["device_id"] == "AA:BB:CC:DD:EE:FF"
    assert merged["pages"] == []
    assert merged["games"] == []
