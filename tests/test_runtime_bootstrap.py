import json

from runtime import bootstrap
from runtime import device_json_identity


def test_ensure_device_info_id_noop_when_id_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(
        json.dumps({"id": "AA:BB:CC:DD:EE:FF"}),
        encoding="utf-8",
    )

    device_info = {"id": "AA:BB:CC:DD:EE:FF"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"


def test_ensure_device_info_id_backfills_from_ble_mac_and_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({"ble_mac": "aa:bb:cc:dd:ee:ff"}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    device_info = {"ble_mac": "aa:bb:cc:dd:ee:ff"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    assert out["serial"] == device_json_identity.FACTORY_PLACEHOLDER_SERIAL
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"
    assert persisted["serial"] == device_json_identity.FACTORY_PLACEHOLDER_SERIAL


def test_ensure_device_info_id_assigns_factory_serial_when_model_only(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(
        json.dumps({"model": "PixelBoard", "brightness": "100"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    device_info = {"id": "AA:BB:CC:DD:EE:FF", "model": "PixelBoard"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    assert out["serial"] == device_json_identity.FACTORY_PLACEHOLDER_SERIAL
    assert out["model"] == "PixelBoard"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["serial"] == device_json_identity.FACTORY_PLACEHOLDER_SERIAL
    assert persisted["model"] == "PixelBoard"
    assert persisted["brightness"] == "100"


def test_ensure_device_info_id_persists_when_incoming_identity_complete(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    device_info = {
        "ble_mac": "aa:bb:cc:dd:ee:ff",
        "serial": "SN-001",
        "model": "PixelDart",
    }
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"
    assert persisted["serial"] == "SN-001"
    assert persisted["model"] == "PixelDart"


def test_ensure_device_info_id_reads_missing_identity_from_boot(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {"serial": "BOOT-SN", "model": "PixelBoard"},
    )

    device_info = {"ble_mac": "aa:bb:cc:dd:ee:ff"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    assert out["serial"] == "BOOT-SN"
    assert out["model"] == "PixelBoard"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"
    assert persisted["serial"] == "BOOT-SN"
    assert persisted["model"] == "PixelBoard"


def test_ensure_device_info_id_backfills_identity_when_id_already_exists(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(
        json.dumps({"id": "AA:BB:CC:DD:EE:FF"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {"serial": "BOOT-SN2", "model": "PixelDart"},
    )

    out = bootstrap._ensure_device_info_id({"id": "AA:BB:CC:DD:EE:FF"})

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    assert out["serial"] == "BOOT-SN2"
    assert out["model"] == "PixelDart"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"
    assert persisted["serial"] == "BOOT-SN2"
    assert persisted["model"] == "PixelDart"


def test_start_background_subsystems_does_not_start_udp_broadcast_thread(monkeypatch):
    started_targets = []

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started_targets.append(self.target)

    class _FakeRemoteSync:
        def set_connectivity_callback(self, _cb):
            return None

        def start_sync_if_available(self, *_args, **_kwargs):
            return None

        def request_set_all_games_ready(self):
            return None

    class _FakeDartsnut:
        def update_frame_buffer(self, _img):
            return None

    class _FakeRemoteConfigRuntime:
        awaiting_games_ready_confirmation = False
        startup_games_ready_confirmed_at = None
        startup_filter_playing_until_newer_update = False
        startup_firmware_version = None

    monkeypatch.setattr(bootstrap.threading, "Thread", _FakeThread)
    monkeypatch.setattr(bootstrap, "get_remote_sync", lambda: _FakeRemoteSync())
    monkeypatch.setattr(bootstrap.assets, "create_loading_image", lambda: object())

    bootstrap.start_background_subsystems(
        dartsnut=_FakeDartsnut(),
        device_info={},
        get_version=lambda: {"version": "1.2.3"},
        set_volume=lambda _v: None,
        start_ble_server=lambda *_a, **_k: None,
        locate_device=lambda: None,
        start_websocket_server=lambda *_a, **_k: None,
        set_brightness=lambda _v: None,
        reload_config=lambda: None,
        set_time_zone=lambda _tz: None,
        get_widgets_framebuffer=lambda: [],
        start_game_from_websocket=lambda _gid: True,
        trigger_dim_check=lambda: None,
        check_connection_loop=lambda: None,
        network_state_remote_loop=lambda: None,
        apply_remote_config=lambda _cfg: None,
        on_remote_connectivity_changed=lambda _c: None,
        request_network_state_refresh=lambda: None,
        remote_config_runtime=_FakeRemoteConfigRuntime(),
    )

    target_names = {getattr(target, "__name__", "") for target in started_targets}
    assert "udp_broadcast" not in target_names
    assert len(started_targets) == 4
