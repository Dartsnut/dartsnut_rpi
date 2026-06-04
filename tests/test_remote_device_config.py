"""Characterization tests for remote-shaped config application."""

import json
import os
from unittest.mock import MagicMock, call

from domain.app_context import AppContext
from runtime.remote_device_config import (
    RemoteConfigRuntimeState,
    RemoteDeviceConfigApplier,
    RemoteDeviceConfigDependencies,
    is_remote_reset_confirmed,
    is_remote_reset_confirmation_source,
    note_local_game_transition,
    parse_iso_ts,
    should_accept_remote_playing_command,
)


# Parsing and reset-shape guards
def test_parse_iso_ts_accepts_z_suffix():
    dt = parse_iso_ts("2026-03-25T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 3 and dt.day == 25
    assert dt.tzinfo is None


def test_parse_iso_ts_normalizes_utc_offset_to_naive():
    dt = parse_iso_ts("2026-05-29T09:16:00+00:00")
    assert dt is not None
    assert dt.tzinfo is None
    assert dt.hour == 9 and dt.minute == 16


def test_should_accept_remote_playing_compares_offset_and_naive_timestamps():
    rt = RemoteConfigRuntimeState()
    note_local_game_transition(rt, "g1", "ready", at=parse_iso_ts("2026-05-29T10:00:00Z"))
    assert not should_accept_remote_playing_command(
        rt, "g1", parse_iso_ts("2026-05-29T09:59:59+00:00"), source="supabase_bridge"
    )
    assert should_accept_remote_playing_command(
        rt, "g1", parse_iso_ts("2026-05-29T10:00:01+00:00"), source="supabase_bridge"
    )


def test_is_remote_reset_confirmed_requires_empty_network_and_dim_disabled():
    assert is_remote_reset_confirmed(
        {
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    )
    assert not is_remote_reset_confirmed(
        {
            "ip_address": "1.2.3.4",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    )


def test_is_remote_reset_confirmation_source_accepts_bridge_and_init_sources():
    assert is_remote_reset_confirmation_source("supabase_bridge")
    assert is_remote_reset_confirmation_source("supabase_bridge_init")
    assert not is_remote_reset_confirmation_source("mobile_app")


def test_apply_confirms_reset_only_for_expected_source():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    confirmations = {"count": 0}
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: True,
        on_reset_confirmed=lambda: confirmations.__setitem__(
            "count", confirmations["count"] + 1
        ),
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    reset_payload = {
        "ip_address": "",
        "ssid": "",
        "pages": [],
        "games": [],
        "dim_window": {"dim_window_enabled": False},
    }
    applier.apply({**reset_payload, "last_update_source": "mobile_app"})
    applier.apply({**reset_payload, "last_update_source": "supabase_bridge_init"})
    assert confirmations["count"] == 1


# Non-game remote config application
def test_apply_sets_reload_flag_when_app_source_pages_change(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)
    with open("apps/conf.json", "w", encoding="utf-8") as f:
        json.dump({"user": "", "pages": []}, f)

    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    ctx.reload_pages = False

    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        disconnect_and_unpair_device=lambda _mac: {},
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    applier.apply(
        {
            "last_update_source": "app",
            "pages": [{"uuid": "p1", "widgets": []}],
        }
    )

    svc.set_pages.assert_called_once()
    args, kwargs = svc.set_pages.call_args
    assert args[0] == [{"uuid": "p1", "widgets": []}]
    assert kwargs.get("reload_pages") is False
    assert ctx.reload_pages is True


def test_apply_app_source_pages_skips_reload_when_fingerprint_unchanged():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    applier.apply(
        {
            "last_update_source": "app",
            "pages": [{"uuid": "p1", "widgets": []}],
        }
    )
    assert ctx.reload_pages is True

    ctx.reload_pages = False
    applier.apply(
        {
            "last_update_source": "app",
            "pages": [{"uuid": "p1", "widgets": []}],
        }
    )
    assert ctx.reload_pages is False


def test_apply_sets_reload_flag_only_for_newer_remote_pages_update():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": "2026-04-22T10:00:00",
            "pages": [{"uuid": "p1", "widgets": []}],
        }
    )
    assert ctx.reload_pages is True

    ctx.reload_pages = False
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": "2026-04-22T10:00:00",
            "pages": [{"uuid": "p1", "widgets": []}],
        }
    )
    assert ctx.reload_pages is False


def test_apply_sets_reload_flag_on_first_supabase_snapshot_without_pages_updated_at_when_pages_differ():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    # Runtime currently has one content page loaded.
    ctx.pages = [
        {"uuid": "existing", "widgets": [], "enabled": True},
        {"uuid": "0", "widgets": [], "enabled": True},
    ]
    ctx.reload_pages = False

    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    # First bridge snapshot does not include pages_updated_at but includes
    # a newly appended page in list order.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages": [
                {"uuid": "existing", "widgets": []},
                {"uuid": "newly-appended", "widgets": []},
            ],
        }
    )

    assert ctx.reload_pages is True


def test_apply_sets_reload_when_pages_updated_at_unchanged_but_pages_fingerprint_differs():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    first_pages = [{"uuid": "p1", "widgets": []}]
    same_ts = "2026-04-27T07:00:00+00:00"
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": same_ts,
            "pages": first_pages,
        }
    )
    assert ctx.reload_pages is True

    # Simulate main loop consuming reload flag between snapshots.
    ctx.reload_pages = False
    second_pages = [
        {"uuid": "p1", "widgets": []},
        {"uuid": "p2", "widgets": []},
    ]
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": same_ts,
            "pages": second_pages,
        }
    )
    assert ctx.reload_pages is True


def test_supabase_pages_skips_full_reload_in_widget_mode_when_soft_apply_succeeds(
    monkeypatch,
):
    from widget_lifecycle import try_soft_apply_remote_supabase_pages

    monkeypatch.setattr(
        "widget_lifecycle.restart_widget_process", lambda *_a, **_k: None
    )

    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    ctx.state_str = "widget"
    ctx.page_index = 0
    ctx.pages = [
        {
            "uuid": "p1",
            "duration": "60",
            "enabled": True,
            "widgets": [
                {
                    "widget": {
                        "id": "w1",
                        "position": [0, 0, 10, 10],
                        "fields": {"x": 1},
                    }
                }
            ],
        }
    ]
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
        try_soft_apply_remote_supabase_pages=try_soft_apply_remote_supabase_pages,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": "2026-04-22T10:00:00",
            "pages": [
                {
                    "uuid": "p1",
                    "duration": "30",
                    "enabled": True,
                    "widgets": [
                        {
                            "id": "w1",
                            "position": [0, 0, 10, 10],
                            "fields": {"x": 1},
                        }
                    ],
                }
            ],
        }
    )
    assert ctx.reload_pages is False
    assert ctx.pages[0]["duration"] == "30"


def test_supabase_pages_soft_apply_updates_active_widget_fields_and_restarts(
    monkeypatch,
):
    from widget_lifecycle import try_soft_apply_remote_supabase_pages

    restarts: list[tuple] = []

    def _track_restart(widget_entry, page, widget_index):
        restarts.append((widget_entry, page["uuid"], widget_index))

    monkeypatch.setattr("widget_lifecycle.restart_widget_process", _track_restart)
    monkeypatch.setattr("widget_lifecycle._kill_widget_process", lambda *_a, **_k: None)

    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    ctx.state_str = "widget"
    ctx.page_index = 0
    ctx.pages = [
        {
            "uuid": "p1",
            "duration": "60",
            "enabled": True,
            "widgets": [
                {
                    "widget": {
                        "id": "w1",
                        "position": [0, 0, 10, 10],
                        "fields": {"x": 1},
                    }
                }
            ],
        }
    ]
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
        try_soft_apply_remote_supabase_pages=try_soft_apply_remote_supabase_pages,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": "2026-04-22T12:00:00",
            "pages": [
                {
                    "uuid": "p1",
                    "duration": "60",
                    "widgets": [
                        {
                            "id": "w1",
                            "position": [0, 0, 10, 10],
                            "fields": {"x": 2},
                        }
                    ],
                }
            ],
        }
    )
    assert ctx.reload_pages is False
    assert len(restarts) == 1
    assert restarts[0][1] == "p1"
    assert restarts[0][2] == 0
    assert ctx.pages[0]["widgets"][0]["widget"]["fields"]["x"] == 2


def test_app_pages_soft_apply_updates_active_widget_fields_without_full_reload(
    monkeypatch,
):
    from widget_lifecycle import try_soft_apply_remote_supabase_pages

    restarts: list[tuple] = []

    def _track_restart(widget_entry, page, widget_index):
        restarts.append((widget_entry, page["uuid"], widget_index))

    monkeypatch.setattr("widget_lifecycle.restart_widget_process", _track_restart)
    monkeypatch.setattr("widget_lifecycle._kill_widget_process", lambda *_a, **_k: None)

    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    ctx.state_str = "widget"
    ctx.page_index = 0
    ctx.pages = [
        {
            "uuid": "p1",
            "duration": "60",
            "enabled": True,
            "widgets": [
                {
                    "widget": {
                        "id": "w1",
                        "position": [0, 0, 10, 10],
                        "fields": {"x": 1},
                    }
                }
            ],
        }
    ]
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
        try_soft_apply_remote_supabase_pages=try_soft_apply_remote_supabase_pages,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())

    applier.apply(
        {
            "last_update_source": "app",
            "pages": [
                {
                    "uuid": "p1",
                    "duration": "60",
                    "widgets": [
                        {
                            "id": "w1",
                            "position": [0, 0, 10, 10],
                            "fields": {"x": 2},
                        }
                    ],
                }
            ],
        }
    )
    assert ctx.reload_pages is False
    assert len(restarts) == 1
    assert restarts[0][1] == "p1"
    assert restarts[0][2] == 0
    assert ctx.pages[0]["widgets"][0]["widget"]["fields"]["x"] == 2


def test_apply_triggers_bluetooth_scan_when_requested():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    applier.apply({"bluetooth": {"is_scan": True}})
    ble.start_scan_if_requested.assert_called_once_with(sync_connected_controllers=True)


def test_apply_is_scan_without_controllers_key_does_not_unpair():
    """Remote scan request must not be treated as an empty controllers list."""
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    disconnect = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
        disconnect_and_unpair_device=disconnect,
    )
    rt = RemoteConfigRuntimeState()
    rt.last_remote_controller_macs = {"AA:BB:CC:DD:EE:01"}
    applier = RemoteDeviceConfigApplier(deps, rt)
    applier.apply({"bluetooth": {"is_scan": True}})
    ble.start_scan_if_requested.assert_called_once_with(sync_connected_controllers=True)
    disconnect.assert_not_called()


def test_apply_calls_apply_explicit_remote_lists_before_starting_scan():
    """Empty controllers from remote must clear local scan state before scan publishes."""
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    cfg = {"bluetooth": {"controllers": [], "is_scan": True}}
    applier.apply(cfg)
    assert ble.mock_calls[0] == call.apply_explicit_remote_lists(cfg["bluetooth"])
    assert ble.mock_calls[1] == call.start_scan_if_requested(
        sync_connected_controllers=True
    )


def test_apply_skips_unpair_while_scan_in_progress():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    disconnect = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
        disconnect_and_unpair_device=disconnect,
    )
    rt = RemoteConfigRuntimeState()
    rt.last_remote_controller_macs = {"98:B6:EE:8D:16:20"}
    applier = RemoteDeviceConfigApplier(deps, rt)
    applier.apply({"bluetooth": {"controllers": [], "is_scan": True}})
    disconnect.assert_not_called()


def test_apply_syncs_connected_controllers_when_remote_list_is_nonempty():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    cfg = {
        "bluetooth": {
            "controllers": [{"mac": "AA:BB:CC:DD:EE:01", "status": "idle"}],
            "is_scan": True,
        }
    }
    applier.apply(cfg)
    assert ble.mock_calls[0] == call.apply_explicit_remote_lists(cfg["bluetooth"])
    assert ble.mock_calls[1] == call.start_scan_if_requested(
        sync_connected_controllers=True
    )


def test_apply_updates_brightness_via_service():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    applier.apply({"brightness": 77})
    svc.set_brightness.assert_called_once_with(77)


def test_apply_bluetooth_connecting_rows_trigger_connect_for_both_lists():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    applier.apply(
        {
            "bluetooth": {
                "controllers": [{"mac": "AA:11", "status": "connecting"}],
                "scan_results": [{"mac": "BB:22", "status": "connecting"}],
            }
        }
    )
    ble.start_connect_if_requested.assert_any_call("AA:11", "controllers")
    ble.start_connect_if_requested.assert_any_call("BB:22", "scan_results")


def test_apply_bluetooth_removed_controller_calls_unpair():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    removed = []
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        disconnect_and_unpair_device=lambda mac: removed.append(mac) or {},
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    runtime = RemoteConfigRuntimeState()
    applier = RemoteDeviceConfigApplier(deps, runtime)
    applier.apply({"bluetooth": {"controllers": [{"mac": "AA:BB", "status": "idle"}]}})
    applier.apply({"bluetooth": {"controllers": []}})
    assert removed == ["AA:BB"]


# Game command handling
def _game_ctx():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    ctx.game = None
    ctx.start_game = False
    ctx.game_id = None
    ctx.reload_conf = False
    return ctx


def test_apply_game_playing_requests_launch():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    status_calls = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda gid, st: status_calls.append((gid, st)),
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "games": [{"id": "g1", "status": "playing", "version": ""}],
        }
    )

    assert game_ctx.start_game is True
    assert game_ctx.game_id == "g1"
    assert game_ctx.reload_game_menu is True


def test_apply_game_playing_switches_running_game_then_launches_new():
    game_ctx = _game_ctx()
    game_ctx.game = {"game_id": "old", "process": MagicMock()}
    svc = MagicMock()
    ble = MagicMock()
    status_calls = []
    term_calls = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda gid, st: status_calls.append((gid, st)),
        set_time_zone=lambda _tz: None,
        term_game_process=lambda g: term_calls.append(g),
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "games": [{"id": "new", "status": "playing", "version": ""}],
        }
    )

    assert len(term_calls) == 1
    assert term_calls[0].get("game_id") == "old"
    assert ("old", "ready") in status_calls
    assert game_ctx.game is None
    assert game_ctx.start_game is True
    assert game_ctx.game_id == "new"


def test_apply_with_games_list_sets_reload_game_menu():
    game_ctx = _game_ctx()
    assert game_ctx.reload_game_menu is False
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)
    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})
    assert game_ctx.reload_game_menu is True


def test_apply_with_empty_games_list_sets_reload_game_menu():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)
    applier.apply({"games": []})
    assert game_ctx.reload_game_menu is True


def test_apply_without_games_key_leaves_reload_game_menu_unchanged():
    game_ctx = _game_ctx()
    game_ctx.reload_game_menu = False
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    applier = RemoteDeviceConfigApplier(deps, RemoteConfigRuntimeState())
    applier.apply({"brightness": 50})
    assert game_ctx.reload_game_menu is False


# Startup settlement gate behavior
def _gate_deps(game_ctx, svc, ble, publish=None, ensure=None):
    return RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=publish or (lambda _p: None),
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=ensure or (lambda _gid, _ver: True),
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )


def test_gate_passes_missing_games_as_empty_no_publish():
    game_ctx = _game_ctx()
    published = []

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(game_ctx, MagicMock(), MagicMock(), publish=published.append),
        rt,
    )

    applier.apply({"last_update_source": "supabase_bridge"})

    assert rt.awaiting_games_ready_confirmation is False
    assert rt.startup_settlement_completed is True
    assert published == []
    assert game_ctx.remote_menu_ready_game_ids == frozenset()


def test_gate_passes_empty_games_no_publish():
    game_ctx = _game_ctx()
    published = []

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(game_ctx, MagicMock(), MagicMock(), publish=published.append),
        rt,
    )

    applier.apply({"games": []})

    assert rt.awaiting_games_ready_confirmation is False
    assert published == []
    assert game_ctx.remote_menu_ready_game_ids == frozenset()


def test_gate_reconciles_stuck_downloading_when_local_matches():
    """First post-restart snapshot skips game commands but still clears downloading."""
    game_ctx = _game_ctx()
    status_updates = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: MagicMock(),
        bluetooth_scan_controller=MagicMock(),
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda gid, st: status_updates.append((gid, st)),
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda *_a: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda gid, ver: gid == "g1" and ver == "2.0.0",
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "updated_at": "2026-04-01T10:00:00",
            "games": [{"id": "g1", "status": "downloading", "version": "2.0.0"}],
        }
    )

    assert status_updates == [("g1", "ready")]
    assert rt.remote_downloading_game_ids == set()
    assert rt.awaiting_games_ready_confirmation is False


def test_gate_playing_publishes_once_then_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps/g1", exist_ok=True)
    game_ctx = _game_ctx()
    published = []

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(game_ctx, MagicMock(), MagicMock(), publish=published.append),
        rt,
    )

    applier.apply(
        {
            "updated_at": "2026-04-01T10:00:00",
            "games": [{"id": "g1", "status": "playing", "version": "1.0"}],
        }
    )

    assert rt.awaiting_games_ready_confirmation is False
    assert {"games": [{"id": "g1", "status": "ready", "version": "1.0"}]} in published
    assert game_ctx.start_game is False


def test_gate_does_not_rearm_on_second_apply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps/g1", exist_ok=True)
    game_ctx = _game_ctx()
    published = []

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(game_ctx, MagicMock(), MagicMock(), publish=published.append),
        rt,
    )

    applier.apply({"games": [{"id": "g1", "status": "ready"}]})
    applier.apply({"games": [{"id": "g1", "status": "playing"}]})

    assert rt.awaiting_games_ready_confirmation is False
    assert len(published) == 0


def test_playing_ignored_while_gate_open():
    game_ctx = _game_ctx()

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(game_ctx, MagicMock(), MagicMock()),
        rt,
    )

    applier.apply({"games": [{"id": "g1", "status": "playing"}]})

    assert game_ctx.start_game is False
    assert rt.awaiting_games_ready_confirmation is False


def test_startup_recovers_missing_ready_games_by_scheduling_download(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    download_calls = []
    published = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda p: published.append(dict(p)),
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda gid, ver: download_calls.append((gid, ver)) or True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})

    assert download_calls == [("g1", "1")]
    assert published == [
        {"games": [{"id": "g1", "version": "1", "status": "downloading"}]},
        {"games": [{"id": "g1", "version": "1", "status": "ready"}]},
    ]
    assert rt.remote_downloading_game_ids == set()


def test_startup_missing_ready_recovery_runs_only_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    download_calls = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda gid, ver: download_calls.append((gid, ver)) or True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})
    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})

    assert download_calls == [("g1", "1")]


def test_startup_missing_ready_recovery_retries_after_failed_download(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    attempts = {"n": 0}
    published = []

    def _ensure(gid, ver):
        attempts["n"] += 1
        return attempts["n"] >= 2

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda p: published.append(dict(p)),
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=_ensure,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})
    assert attempts["n"] == 1
    assert rt.startup_missing_ready_games_recovery_done is False
    assert published == [
        {"games": [{"id": "g1", "version": "1", "status": "downloading"}]},
    ]

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})
    assert attempts["n"] == 2
    assert rt.startup_missing_ready_games_recovery_done is True
    assert published == [
        {"games": [{"id": "g1", "version": "1", "status": "downloading"}]},
        {"games": [{"id": "g1", "version": "1", "status": "downloading"}]},
        {"games": [{"id": "g1", "version": "1", "status": "ready"}]},
    ]


def test_startup_recovery_downloads_playing_game_missing_after_settlement(
    tmp_path, monkeypatch
):
    """Playing games settled to ready on the same snapshot must still be recovered."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps/chess", exist_ok=True)

    game_ctx = _game_ctx()
    download_calls = []
    published = []

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(
            game_ctx,
            MagicMock(),
            MagicMock(),
            ensure=lambda gid, ver: download_calls.append((gid, ver)) or True,
            publish=lambda p: published.append(dict(p)),
        ),
        rt,
    )

    applier.apply(
        {
            "updated_at": "2026-04-01T12:00:00",
            "games": [
                {"id": "cricket", "status": "playing", "version": "2.0.0"},
                {"id": "chess", "status": "ready", "version": "1.0.0"},
            ],
        }
    )

    assert download_calls == [("cricket", "2.0.0")]
    assert {"games": [{"id": "cricket", "version": "2.0.0", "status": "downloading"}]} in published
    assert {"games": [{"id": "cricket", "version": "2.0.0", "status": "ready"}]} in published
    assert {"games": [{"id": "cricket", "status": "ready", "version": "2.0.0"}]} in published


def test_startup_missing_ready_recovery_runs_on_settlement_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    game_ctx = _game_ctx()
    download_calls = []

    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(
            game_ctx,
            MagicMock(),
            MagicMock(),
            ensure=lambda gid, ver: download_calls.append((gid, ver)) or True,
        ),
        rt,
    )

    applier.apply(
        {
            "updated_at": "2026-04-01T12:00:00",
            "games": [{"id": "g1", "status": "ready", "version": "1"}],
        }
    )

    assert rt.awaiting_games_ready_confirmation is False
    assert download_calls == [("g1", "1")]


def test_startup_missing_ready_recovery_runs_on_gate_confirmation_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    download_calls = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda gid, ver: download_calls.append((gid, ver)) or True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T13:00:00",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False
    assert download_calls == [("g1", "1")]

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T13:00:01",
            "games": [{"id": "g1", "status": "ready", "version": "1"}],
        }
    )
    assert download_calls == [("g1", "1")]


def test_startup_missing_ready_recovery_confirms_when_timestamps_missing(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    download_calls = []

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda gid, ver: download_calls.append((gid, ver)) or True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False
    assert download_calls == [("g1", "1")]

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "games": [{"id": "g1", "status": "ready", "version": "1"}],
        }
    )
    assert download_calls == [("g1", "1")]


# Stale remote playing / bad-network replay guards
def _playing_guard_applier(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps/g1", exist_ok=True)
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    rt = RemoteConfigRuntimeState()
    rt.startup_settlement_completed = True
    rt.awaiting_games_ready_confirmation = False
    return RemoteDeviceConfigApplier(deps, rt), game_ctx


def test_reconnect_burst_stale_playing_snapshots_do_not_launch(tmp_path, monkeypatch):
    applier, game_ctx = _playing_guard_applier(tmp_path, monkeypatch)
    os.makedirs("apps/g2", exist_ok=True)
    local_ts = parse_iso_ts("2026-05-28T20:00:00")
    note_local_game_transition(applier.runtime, "g1", "ready", at=local_ts)
    note_local_game_transition(applier.runtime, "g2", "ready", at=local_ts)

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-05-28T18:00:00",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert game_ctx.start_game is False

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-05-28T18:00:01",
            "games": [{"id": "g2", "status": "playing", "version": "1"}],
        }
    )
    assert game_ctx.start_game is False


def test_stale_playing_after_local_exit_does_not_relaunch(tmp_path, monkeypatch):
    applier, game_ctx = _playing_guard_applier(tmp_path, monkeypatch)
    note_local_game_transition(
        applier.runtime, "g1", "ready", at=parse_iso_ts("2026-05-29T09:16:00")
    )

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-05-29T09:15:57",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert game_ctx.start_game is False


def test_newer_remote_playing_after_local_exit_launches(tmp_path, monkeypatch):
    applier, game_ctx = _playing_guard_applier(tmp_path, monkeypatch)
    note_local_game_transition(
        applier.runtime, "g1", "ready", at=parse_iso_ts("2026-05-29T09:16:00")
    )

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-05-29T09:16:05",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert game_ctx.start_game is True
    assert game_ctx.game_id == "g1"


def test_duplicate_identical_bridge_snapshot_suppresses_second_launch(
    tmp_path, monkeypatch
):
    applier, game_ctx = _playing_guard_applier(tmp_path, monkeypatch)
    cfg = {
        "last_update_source": "supabase_bridge",
        "updated_at": "2026-05-29T09:16:00",
        "games": [{"id": "g1", "status": "playing", "version": "1"}],
    }
    applier.apply(cfg)
    assert game_ctx.start_game is True
    game_ctx.start_game = False
    game_ctx.game_id = None

    applier.apply(dict(cfg))
    assert game_ctx.start_game is False


def test_should_accept_remote_playing_requires_strictly_newer_than_local():
    rt = RemoteConfigRuntimeState()
    note_local_game_transition(rt, "g1", "ready", at=parse_iso_ts("2026-05-29T10:00:00"))
    assert not should_accept_remote_playing_command(
        rt, "g1", parse_iso_ts("2026-05-29T09:59:59"), source="supabase_bridge"
    )
    assert should_accept_remote_playing_command(
        rt, "g1", parse_iso_ts("2026-05-29T10:00:01"), source="supabase_bridge"
    )


def test_monotonic_guard_does_not_block_reset_confirmation():
    ctx = AppContext(
        display=MagicMock(),
        assets=MagicMock(),
        get_device_info=lambda: {},
        set_brightness=lambda _b: None,
        set_volume=lambda _v: None,
    )
    svc = MagicMock()
    ble = MagicMock()
    confirmations = {"count": 0}
    deps = RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: True,
        on_reset_confirmed=lambda: confirmations.__setitem__(
            "count", confirmations["count"] + 1
        ),
    )
    rt = RemoteConfigRuntimeState()
    note_local_game_transition(rt, "g1", "ready", at=parse_iso_ts("2026-05-29T12:00:00"))
    applier = RemoteDeviceConfigApplier(deps, rt)
    reset_payload = {
        "ip_address": "",
        "ssid": "",
        "pages": [],
        "games": [],
        "dim_window": {"dim_window_enabled": False},
        "updated_at": "2026-05-28T10:00:00",
        "last_update_source": "supabase_bridge",
    }
    applier.apply(reset_payload)
    assert confirmations["count"] == 1


def test_gate_settlement_still_ignores_playing_commands(tmp_path, monkeypatch):
    """Startup gate settles playing->ready; monotonic guard must not launch during gate."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps/g1", exist_ok=True)
    game_ctx = _game_ctx()
    published = []
    rt = RemoteConfigRuntimeState()
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(
        _gate_deps(game_ctx, MagicMock(), MagicMock(), publish=published.append),
        rt,
    )
    note_local_game_transition(
        rt, "g1", "ready", at=parse_iso_ts("2026-05-29T12:00:00")
    )
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-05-28T10:00:00",
            "games": [{"id": "g1", "status": "playing", "version": "1.0"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False
    assert {"games": [{"id": "g1", "status": "ready", "version": "1.0"}]} in published
    assert game_ctx.start_game is False

