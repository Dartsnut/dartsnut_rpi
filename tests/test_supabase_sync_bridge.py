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
    assert state["bluetooth"] == {
        "is_scan": False,
        "controllers": [],
        "scan_results": [],
        "last_scan_at": "",
    }
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


def test_normalize_config_payload_defaults_bridge_source():
    out = ssb._normalize_config_payload({"pages": [], "games": []})
    assert out["last_update_source"] == "supabase_bridge"


def test_coerce_bluetooth_schema_is_scan_only_does_not_emit_empty_controller_lists():
    out = ssb._coerce_bluetooth_schema({"bluetooth": {"is_scan": True}})
    assert out["bluetooth"] == {"is_scan": True}


def test_coerce_pages_games_lists_does_not_invent_bluetooth():
    out = ssb._coerce_pages_games_lists({"brightness": 70})
    assert "bluetooth" not in out


def test_merge_remote_and_local_preserves_device_id(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(ssb, "_build_initial_state", lambda _d: {"device_info": {"id": "AA:BB:CC:DD:EE:FF"}})
    merged = ssb._merge_remote_and_local({"pages": None, "games": None})
    assert merged["device_info"]["id"] == "AA:BB:CC:DD:EE:FF"
    assert merged["pages"] == []
    assert merged["games"] == []
    assert merged["last_update_source"] == "supabase_bridge"



def test_merge_remote_and_local_does_not_replace_remote_games_with_local_newer_device(
    monkeypatch,
):
    monkeypatch.setattr(
        ssb,
        "_build_initial_state",
        lambda _d: {
            "time_zone": "UTC",
            "volume": 10,
            "brightness": 20,
            "games": [{"id": "local-only", "version": "1.0.0", "status": "ready"}],
            "dim_window": {"dim_window_enabled": False},
            "device_info": {"id": "AA:BB:CC:DD:EE:FF"},
            "firmware": {"version": "local-fw", "update": False},
        },
    )
    monkeypatch.setattr(ssb, "_load_device_json", lambda: {"updated_at": "2026-04-23T12:00:00"})
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)

    remote = {
        "device_updated_at": "2026-04-23T11:00:00",
        "games": [
            {"id": "01dartgame", "version": "1.0.6", "status": "ready"},
            {"id": "cricket", "version": "1.2.0", "status": "ready"},
            {"id": "splashgame", "version": "1.0.1", "status": "ready"},
        ],
    }
    merged = ssb._merge_remote_and_local(remote)
    assert [g.get("id") for g in merged["games"]] == [
        "01dartgame",
        "cricket",
        "splashgame",
    ]


def test_load_device_json_reads_device_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(tmp_path / "device.json", "w", encoding="utf-8") as f:
        json.dump({"id": "11:22:33:44:55:66", "name": "Lab"}, f)
    assert ssb._load_device_json() == {"id": "11:22:33:44:55:66", "name": "Lab"}


def test_request_device_reset_state_includes_device_info_from_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ssb, "resolve_pixeldarts_hardware_version", lambda: "")
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
    captured_source = []
    monkeypatch.setattr(
        ssb,
        "publish_device_state_update",
        lambda p, source=None: (captured.append(p), captured_source.append(source)),
    )
    ssb.request_device_reset_state()
    assert len(captured) == 1
    assert captured_source == ["supabase_bridge_init"]
    assert captured[0]["device_info"] == {
        "id": "AA:BB:CC:DD:EE:FF",
        "sn": "S1",
        "model": "PixelDart",
        "name": "Den",
    }


def test_sync_client_send_state_includes_source_when_present():
    client = ssb._SyncClient(
        socket_path="/tmp/unused.sock",
        reload_config=lambda: None,
        on_config_updated=lambda _cfg: None,
        initial_state={},
    )

    class _Conn:
        def __init__(self):
            self.writes = []

        def sendall(self, data):
            self.writes.append(data)

    conn = _Conn()
    client._conn = conn
    ok = client.send_state({"brightness": 70}, source="supabase_bridge_init")
    assert ok is True
    sent = json.loads(conn.writes[0].decode("utf-8").strip())
    assert sent["kind"] == "device_state"
    assert sent["payload"]["brightness"] == 70
    assert sent["source"] == "supabase_bridge_init"


def test_build_initial_state_prefers_lsusb_over_stale_device_json(monkeypatch):
    monkeypatch.setattr(ssb, "resolve_pixeldarts_hardware_version", lambda: "444f")
    state = ssb._build_initial_state({"hardware_version": "444e", "id": "AA:BB:CC:DD:EE:FF"})
    assert state["device_info"]["hardware_version"] == "444f"


def test_build_initial_state_adds_hardware_version_when_missing(monkeypatch):
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(ssb, "resolve_pixeldarts_hardware_version", lambda: "444e")

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


def test_restart_supabase_sync_skips_restart_when_already_connected(monkeypatch):
    calls = []
    monkeypatch.setattr(ssb, "is_supabase_bridge_active", lambda: True)
    monkeypatch.setattr(ssb, "is_supabase_connected", lambda: True)
    monkeypatch.setattr(ssb, "stop_supabase_sync", lambda: calls.append("stop"))
    monkeypatch.setattr(
        ssb,
        "ensure_supabase_sync_running",
        lambda *_args, **_kwargs: calls.append("ensure"),
    )

    ssb.restart_supabase_sync({}, lambda: None, lambda _cfg: None)

    assert calls == []


def test_bridge_health_updates_rest_probe_cache():
    with ssb._rest_probe_lock:
        ssb._last_rest_latency_ms = None
        ssb._last_rest_probe_ok = False

    ssb._update_rest_probe_cache(
        {
            "state": "connected",
            "rest_latency_ms": 142,
            "rest_probe_ok": True,
        }
    )

    assert ssb.get_supabase_rest_latency_ms() == 142
    assert ssb.get_supabase_rest_probe_ok() is True


def test_bridge_health_probe_failure_clears_latency():
    ssb._update_rest_probe_cache(
        {
            "state": "disconnected",
            "rest_probe_ok": False,
            "rest_latency_ms": None,
        }
    )

    assert ssb.get_supabase_rest_probe_ok() is False
    assert ssb.get_supabase_rest_latency_ms() is None


def _picture_page(uuid: str, image_b64: str) -> dict:
    return {
        "uuid": uuid,
        "title": f"Page {uuid[:4]}",
        "enabled": True,
        "widgets": [
            {
                "id": "simple_picture_128_128",
                "fields": {"image": {"image": image_b64}},
                "position": [0, 0, 127, 127],
            }
        ],
        "duration": "15",
        "combination": "0",
    }


def test_sanitize_keeps_page_with_short_embedded_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    apps = tmp_path / "apps"
    apps.mkdir()
    short_image = "a" * 100
    conf = {
        "ssid": "TestNet",
        "pages": [_picture_page("good-page", short_image)],
        "pages_updated_at": "2026-01-01T00:00:00",
    }
    conf_path = apps / "conf.json"
    conf_path.write_text(json.dumps(conf), encoding="utf-8")

    pages, removed = ssb._sanitize_apps_conf_pages_for_supabase_sync()

    assert removed == 0
    assert len(pages) == 1
    assert pages[0]["uuid"] == "good-page"
    on_disk = json.loads(conf_path.read_text(encoding="utf-8"))
    assert len(on_disk["pages"]) == 1
    assert on_disk["pages_updated_at"] == "2026-01-01T00:00:00"


def test_sanitize_removes_page_with_oversized_embedded_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    apps = tmp_path / "apps"
    apps.mkdir()
    long_image = "i" * 501
    conf = {
        "ssid": "TestNet",
        "games": [{"id": "cricket", "status": "ready"}],
        "pages": [_picture_page("bad-page", long_image)],
        "pages_updated_at": "2026-01-01T00:00:00",
    }
    conf_path = apps / "conf.json"
    conf_path.write_text(json.dumps(conf), encoding="utf-8")

    pages, removed = ssb._sanitize_apps_conf_pages_for_supabase_sync()

    assert removed == 1
    assert pages == []
    on_disk = json.loads(conf_path.read_text(encoding="utf-8"))
    assert on_disk["pages"] == []
    assert on_disk["ssid"] == "TestNet"
    assert on_disk["games"] == [{"id": "cricket", "status": "ready"}]
    assert on_disk["pages_updated_at"] != "2026-01-01T00:00:00"


def test_build_initial_state_strips_oversized_pages_from_conf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ssb.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(ssb, "resolve_pixeldarts_hardware_version", lambda: "")

    apps = tmp_path / "apps"
    apps.mkdir()
    good = _picture_page("good-page", "x" * 50)
    bad = _picture_page("bad-page", "y" * 600)
    clock_page = {
        "uuid": "clock-page",
        "title": "Clock",
        "enabled": True,
        "widgets": [
            {
                "id": "digitalclock",
                "fields": {"hourColor": ""},
                "position": [0, 0, 127, 127],
            }
        ],
        "duration": "15",
        "combination": "0",
    }
    conf_path = apps / "conf.json"
    conf_path.write_text(
        json.dumps({"pages": [good, bad, clock_page], "pages_updated_at": ""}),
        encoding="utf-8",
    )

    state = ssb._build_initial_state({"id": "AA:BB:CC:DD:EE:FF", "brightness": "70"})

    assert len(state["pages"]) == 2
    uuids = {p["uuid"] for p in state["pages"]}
    assert uuids == {"good-page", "clock-page"}
    on_disk = json.loads(conf_path.read_text(encoding="utf-8"))
    assert {p["uuid"] for p in on_disk["pages"]} == uuids
    assert state["pages_updated_at"] == on_disk["pages_updated_at"]
    assert state["pages_updated_at"] != ""
