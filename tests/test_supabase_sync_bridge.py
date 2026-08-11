import json
import os
import socket
import time

import supabase_sync_bridge as ssb
from runtime.game_secret_store import clear_game_secrets, get_pico8_key


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
    assert state["brightness"] == 7
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
    assert "time_zone" not in state
    assert state["device_info"] == {
        "id": "AA:BB:CC:DD:EE:FF",
        "sn": "SN123",
        "model": "PixelBoard",
        "name": "Kitchen",
    }
    assert state["firmware"] == {"version": "1.2.3", "update": False}


def test_build_initial_state_games_match_sync_visible_local_catalog(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ssb, "resolve_pixeldarts_hardware_version", lambda: "")
    expected_games = {
        "flipdarts": "1.0.0",
        "01dartgame": "1.0.1",
        "pico8": "1.0.2",
        "cricket": "2.0.0",
        "splashgame": "3.0.0",
    }
    for game_id, version in expected_games.items():
        game_dir = tmp_path / "apps" / game_id
        game_dir.mkdir(parents=True)
        (game_dir / "conf.json").write_text(
            json.dumps(
                {
                    "id": game_id,
                    "name": game_id,
                    "type": "game",
                    "version": version,
                }
            ),
            encoding="utf-8",
        )

    state = ssb._build_initial_state({"id": "AA:BB:CC:DD:EE:FF"})

    games_by_id = {g["id"]: g for g in state["games"]}
    assert set(games_by_id) == set(expected_games)
    assert {gid: g["version"] for gid, g in games_by_id.items()} == expected_games
    assert all(g["status"] == "ready" for g in games_by_id.values())


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


def test_sanitize_firmware_partial_patch_drops_read_only_fields():
    out = ssb._sanitize_firmware_partial_patch(
        {
            "brightness": 70,
            "time_zone": "Asia/Taipei",
            "dim_window": {"dim_window_enabled": True},
            "pages": [{"uuid": "p1"}],
            "pages_updated_at": "2026-01-01T00:00:00Z",
            "device_info": {
                "id": "AA:BB:CC:DD:EE:FF",
                "sn": "S1",
                "model": "PixelDart",
                "hardware_version": "2a",
                "name": "Kitchen",
            },
            "firmware": {"version": "1.2.3", "update": False, "channel": "beta"},
        }
    )

    assert out == {
        "brightness": 70,
        "pages": [{"uuid": "p1"}],
        "pages_updated_at": "2026-01-01T00:00:00Z",
        "device_info": {
            "id": "AA:BB:CC:DD:EE:FF",
            "sn": "S1",
            "model": "PixelDart",
            "hardware_version": "2a",
        },
        "firmware": {"version": "1.2.3", "update": False},
    }


def test_sanitize_firmware_partial_patch_keeps_game_identity_only_for_lookup():
    out = ssb._sanitize_firmware_partial_patch(
        {
            "games": [
                {
                    "id": "chess",
                    "name": "Chess",
                    "status": "PLAYING",
                    "version": "1.0.0",
                    "url": "https://example.test/game.zip",
                },
                {"id": "empty-status"},
                {"status": "ready"},
            ]
        }
    )

    assert out == {
        "games": [{"id": "chess", "status": "playing", "version": "1.0.0"}]
    }


def test_sanitize_firmware_partial_patch_strips_pico8_key():
    out = ssb._sanitize_firmware_partial_patch(
        {
            "games": [
                {
                    "id": "pico8",
                    "status": "ready",
                    "version": "1.0.0",
                    "key": "secret-key",
                }
            ]
        }
    )

    assert out == {
        "games": [{"id": "pico8", "status": "ready", "version": "1.0.0"}]
    }


def test_remember_remote_game_ids_stores_inbound_pico8_key():
    clear_game_secrets()

    ssb._remember_remote_game_ids(
        {
            "games": [
                {"id": "chess", "key": "ignored"},
                {"id": "pico8", "status": "ready", "key": "secret-key"},
            ]
        }
    )

    assert get_pico8_key() == "secret-key"


def test_request_set_game_status_does_not_publish_pico8_key(monkeypatch):
    clear_game_secrets()
    ssb._remember_remote_game_ids(
        {"games": [{"id": "pico8", "version": "1.0.0", "key": "secret-key"}]}
    )
    captured = []
    monkeypatch.setattr(ssb, "publish_device_state_update", lambda payload: captured.append(payload))
    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [{"id": "pico8", "version": "1.0.0", "status": "ready"}],
    )
    monkeypatch.setattr(
        "game_lifecycle.resolve_game_version_for_sync",
        lambda _gid, remote_version: remote_version,
    )

    ssb.request_set_game_status("pico8", "ready")

    assert captured == [
        {"games": [{"id": "pico8", "version": "1.0.0", "status": "ready"}]}
    ]


def test_publish_device_state_update_sanitizes_partial_payload(monkeypatch):
    captured = []

    class _Engine:
        def publish_partial(self, payload, source=None):
            captured.append((payload, source))

    monkeypatch.setattr(ssb, "_sync_engine", _Engine())
    ssb.publish_device_state_update(
        {
            "volume": 22,
            "device_info": {"id": "AA:BB:CC:DD:EE:FF", "name": "Kitchen"},
            "dim_window": {"dim_window_enabled": True},
        },
        source="test",
    )

    assert captured == [
        (
            {"volume": 22, "device_info": {"id": "AA:BB:CC:DD:EE:FF"}},
            "test",
        )
    ]


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
            "volume": 10,
            "brightness": 20,
            "games": [{"id": "local-only", "version": "1.0.0", "status": "ready"}],
            "dim_window": {"dim_window_enabled": False},
            "device_info": {"id": "AA:BB:CC:DD:EE:FF", "name": "Local"},
            "firmware": {"version": "local-fw", "update": False},
        },
    )
    monkeypatch.setattr(ssb, "_load_device_json", lambda: {"updated_at": "2026-04-23T12:00:00"})
    monkeypatch.setattr(ssb.os.path, "isfile", lambda _p: False)

    remote = {
        "device_updated_at": "2026-04-23T11:00:00",
        "time_zone": "Asia/Taipei",
        "dim_window": {"dim_window_enabled": True},
        "device_info": {"id": "AA:BB:CC:DD:EE:FF", "name": "Remote"},
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
    assert merged["time_zone"] == "Asia/Taipei"
    assert merged["dim_window"] == {"dim_window_enabled": True}
    assert merged["device_info"]["name"] == "Remote"


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
    engine = __import__(
        "runtime.sync.engine", fromlist=["SyncEngine"]
    ).SyncEngine(
        on_apply_config=lambda _cfg: None,
        merge_on_first_connect=lambda cfg: cfg,
        normalize_config=ssb._normalize_config_payload,
        remember_remote_game_ids=lambda _cfg: None,
    )
    client = ssb._SyncClient(
        socket_path="/tmp/unused.sock",
        reload_config=lambda: None,
        on_config_updated=lambda _cfg: None,
        initial_state={},
        sync_engine=engine,
    )

    class _Conn:
        def __init__(self):
            self.writes = []

        def sendall(self, data):
            self.writes.append(data)

    conn = _Conn()
    client._conn = conn
    ok = client.send_state({"brightness": 7}, source="supabase_bridge_init")
    assert ok is True
    sent = json.loads(conn.writes[0].decode("utf-8").strip())
    assert sent["kind"] in ("device_state", "rpc_patch")
    assert sent["payload"]["brightness"] == 7
    assert sent["source"] == "supabase_bridge_init"


def test_sync_engine_preserves_remote_user_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    engine = __import__(
        "runtime.sync.engine", fromlist=["SyncEngine"]
    ).SyncEngine(
        on_apply_config=lambda _cfg: None,
        merge_on_first_connect=lambda cfg: cfg,
        normalize_config=ssb._normalize_config_payload,
        remember_remote_game_ids=lambda _cfg: None,
    )

    engine.ingest_remote_row({"user": {"token": "abc"}, "updated_at": "2026-06-01T00:00:00Z"})

    from runtime.api_token_store import get_api_token

    assert get_api_token() == "abc"

    engine.ingest_remote_row({"brightness": 50, "updated_at": "2026-06-01T00:00:01Z"})
    assert get_api_token() == "abc"

    engine.ingest_remote_row({"user": {"token": ""}, "updated_at": "2026-06-01T00:00:02Z"})
    assert get_api_token() == ""


def test_sync_client_ready_still_sends_initial_state(tmp_path):
    socket_path = f"/tmp/dn-sync-{time.monotonic_ns()}.sock"
    engine = __import__(
        "runtime.sync.engine", fromlist=["SyncEngine"]
    ).SyncEngine(
        on_apply_config=lambda _cfg: None,
        merge_on_first_connect=lambda cfg: cfg,
        normalize_config=ssb._normalize_config_payload,
        remember_remote_game_ids=lambda _cfg: None,
    )
    client = ssb._SyncClient(
        socket_path=socket_path,
        reload_config=lambda: None,
        on_config_updated=lambda _cfg: None,
        initial_state={"volume": 50, "brightness": 4},
        sync_engine=engine,
    )
    client.start_server()

    deadline = time.monotonic() + 2.0
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    while True:
        try:
            conn.connect(socket_path)
            break
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    try:
        conn.settimeout(2.0)
        conn.sendall(b'{"kind":"ready","payload":{}}\n')
        sent = json.loads(conn.recv(4096).decode("utf-8").strip())
    finally:
        conn.close()
        try:
            os.remove(socket_path)
        except OSError:
            pass

    assert sent["kind"] == "initial_state"
    assert sent["full"] is True
    assert sent["payload"]["volume"] == 50
    assert sent["payload"]["brightness"] == 4


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


# Staleness watchdog: detect a bridge that is alive + flagged connected but silent.
class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def test_bridge_not_stale_when_no_process(monkeypatch):
    with ssb._bridge_lock:
        ssb._bridge_proc = None
    monkeypatch.setattr(ssb, "is_supabase_bridge_active", lambda: True)
    assert ssb.is_supabase_bridge_stale() is False


def test_bridge_not_stale_with_recent_activity(monkeypatch):
    monkeypatch.setattr(ssb, "is_supabase_bridge_active", lambda: True)
    with ssb._bridge_lock:
        ssb._bridge_proc = _FakeProc(alive=True)
    with ssb._bridge_activity_lock:
        ssb._last_bridge_activity_at = time.monotonic()
    try:
        assert ssb.is_supabase_bridge_stale() is False
    finally:
        with ssb._bridge_lock:
            ssb._bridge_proc = None


def test_bridge_stale_when_silent_past_window(monkeypatch):
    monkeypatch.setattr(ssb, "is_supabase_bridge_active", lambda: True)
    with ssb._bridge_lock:
        ssb._bridge_proc = _FakeProc(alive=True)
    with ssb._bridge_activity_lock:
        ssb._last_bridge_activity_at = (
            time.monotonic() - ssb._BRIDGE_STALE_SECONDS - 10.0
        )
    try:
        assert ssb.is_supabase_bridge_stale() is True
    finally:
        with ssb._bridge_lock:
            ssb._bridge_proc = None


def test_bridge_stale_when_process_exited(monkeypatch):
    # A dead process while the client still considers the bridge active must be
    # recycled: nothing else periodically respawns it (the writer thread can force
    # exit on a stuck write).
    monkeypatch.setattr(ssb, "is_supabase_bridge_active", lambda: True)
    with ssb._bridge_lock:
        ssb._bridge_proc = _FakeProc(alive=False)
    with ssb._bridge_activity_lock:
        ssb._last_bridge_activity_at = time.monotonic()
    try:
        assert ssb.is_supabase_bridge_stale() is True
    finally:
        with ssb._bridge_lock:
            ssb._bridge_proc = None


def test_record_bridge_activity_updates_clock():
    with ssb._bridge_activity_lock:
        ssb._last_bridge_activity_at = None
    ssb._record_bridge_activity()
    assert ssb._seconds_since_bridge_activity() is not None
    assert ssb._seconds_since_bridge_activity() < 5.0


def test_restart_supabase_sync_recycles_stale_bridge(monkeypatch):
    calls = []
    monkeypatch.setattr(ssb, "is_supabase_bridge_active", lambda: True)
    monkeypatch.setattr(ssb, "is_supabase_connected", lambda: True)
    monkeypatch.setattr(ssb, "is_supabase_bridge_stale", lambda: True)
    monkeypatch.setattr(ssb, "stop_supabase_sync", lambda: calls.append("stop"))
    monkeypatch.setattr(
        ssb,
        "ensure_supabase_sync_running",
        lambda *_args, **_kwargs: calls.append("ensure"),
    )

    ssb.restart_supabase_sync({}, lambda: None, lambda _cfg: None)

    # Stale bridge must be torn down and relaunched even though it reports connected.
    assert calls == ["stop", "ensure"]
