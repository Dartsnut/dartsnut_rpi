"""Characterization tests for remote-shaped config application."""

import json
import os
from unittest.mock import MagicMock

from domain.app_context import AppContext
from runtime.remote_device_config import (
    RemoteConfigRuntimeState,
    RemoteDeviceConfigApplier,
    RemoteDeviceConfigDependencies,
    is_remote_reset_confirmed,
    parse_iso_ts,
)


# Parsing and reset-shape guards
def test_parse_iso_ts_accepts_z_suffix():
    dt = parse_iso_ts("2026-03-25T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 3 and dt.day == 25


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


# Non-game remote config application
def test_apply_sets_pages_without_reload_when_not_remote_update(
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
        request_set_all_games_ready=lambda: None,
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
    applier.apply({"pages": [{"uuid": "p1", "widgets": []}]})

    svc.set_pages.assert_called_once()
    args, kwargs = svc.set_pages.call_args
    assert args[0] == [{"uuid": "p1", "widgets": []}]
    assert kwargs.get("reload_pages") is False
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
        request_set_all_games_ready=lambda: None,
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
        request_set_all_games_ready=lambda: None,
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
        request_set_all_games_ready=lambda: None,
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
        request_set_all_games_ready=lambda: None,
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
    ble.start_scan_if_requested.assert_called_once()


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
        request_set_all_games_ready=lambda: None,
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
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
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
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
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
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
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
        request_set_all_games_ready=lambda: None,
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


# Startup gate behavior
def test_startup_ready_confirmation_waits_on_supabase_bridge_updates_without_games():
    """
    If inbound snapshots are sourced from `supabase_bridge` but omit `games`,
    startup ready-confirmation should not complete yet.
    """
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    all_ready = {"count": 0}

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: all_ready.__setitem__("count", all_ready["count"] + 1),
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
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"last_update_source": "supabase_bridge"})
    applier.apply({"last_update_source": "supabase_bridge"})

    assert rt.awaiting_games_ready_confirmation is True
    assert rt.startup_games_ready_confirmed_at is None
    assert all_ready["count"] == 1


def test_startup_always_requests_all_ready_even_if_last_source_is_device():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    all_ready = {"count": 0}

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: all_ready.__setitem__("count", all_ready["count"] + 1),
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"last_update_source": "device", "games": [{"id": "g1", "status": "ready"}]})

    assert rt.awaiting_games_ready_confirmation is True
    assert all_ready["count"] == 1


def test_startup_requests_config_refresh_once_on_gate_initialization():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    refresh_calls = {"count": 0}

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: None,
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: None,
        ensure_game_downloaded=lambda _gid, _ver: True,
        cancel_game_download=lambda _gid: None,
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {},
        get_version=lambda: {},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
        request_config_refresh=lambda: refresh_calls.__setitem__(
            "count", refresh_calls["count"] + 1
        ),
    )
    rt = RemoteConfigRuntimeState()
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"games": [{"id": "g1", "status": "playing"}]})
    applier.apply({"games": [{"id": "g1", "status": "ready"}]})

    assert refresh_calls["count"] == 1


def test_startup_ready_confirmation_requires_newer_timestamp_and_all_ready():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    all_ready = {"count": 0}

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: all_ready.__setitem__("count", all_ready["count"] + 1),
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    # Startup pass should trigger reset request and mark pending.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:00",
            "games": [{"id": "g1", "status": "playing"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True
    assert all_ready["count"] == 1

    # Same timestamp + all ready should still not confirm.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:00",
            "games": [{"id": "g1", "status": "ready"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True

    # Newer timestamp + still not all-ready should not confirm.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:01",
            "games": [{"id": "g1", "status": "playing"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True

    # Newer timestamp + all-ready confirms.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:02",
            "games": [{"id": "g1", "status": "ready"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False


def test_startup_gate_passes_on_newer_timestamp_when_all_games_are_ready_or_downloading():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    all_ready = {"count": 0}

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: all_ready.__setitem__("count", all_ready["count"] + 1),
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T10:00:00",
            "games": [{"id": "g1", "status": "playing"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T10:00:01",
            "games": [
                {"id": "g1", "status": "downloading", "version": "2.0.0"},
                {"id": "g2", "status": "ready"},
            ],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False


def test_startup_gate_keeps_pending_when_downloading_present_but_timestamp_not_newer():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()
    all_ready = {"count": 0}

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: all_ready.__setitem__("count", all_ready["count"] + 1),
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T11:00:00",
            "games": [{"id": "g1", "status": "playing"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True

    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T11:00:00",
            "games": [{"id": "g1", "status": "downloading", "version": "3.0.0"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True


def test_startup_gate_prefers_updated_at_over_device_updated_at_for_newer_check():
    game_ctx = _game_ctx()
    svc = MagicMock()
    ble = MagicMock()

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: None,
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    # First snapshot initializes gate baseline at updated_at=10:00:00.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T10:00:00",
            "device_updated_at": "2026-04-01T09:00:00",
            "games": [{"id": "g1", "status": "playing"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True

    # device_updated_at remains stale, but updated_at is newer and all games are stable.
    # Gate must use updated_at so it can pass.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T10:00:01",
            "device_updated_at": "2026-04-01T09:00:00",
            "games": [{"id": "g1", "status": "ready"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False


def test_startup_recovers_missing_ready_games_by_scheduling_download(tmp_path, monkeypatch):
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
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})

    assert download_calls == [("g1", "1")]


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
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
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

    def _ensure(gid, ver):
        attempts["n"] += 1
        return attempts["n"] >= 2

    deps = RemoteDeviceConfigDependencies(
        app_ctx=game_ctx,
        get_machine_state_service=lambda: svc,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda _p: None,
        request_set_game_status=lambda *_a: None,
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})
    assert attempts["n"] == 1
    assert rt.startup_missing_ready_games_recovery_done is False

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": "1"}]})
    assert attempts["n"] == 2
    assert rt.startup_missing_ready_games_recovery_done is True


def test_startup_missing_ready_recovery_waits_until_gate_completes(tmp_path, monkeypatch):
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
        request_set_all_games_ready=lambda: None,
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
    rt.startup_games_reset_initialized = True
    rt.awaiting_games_ready_confirmation = True
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "updated_at": "2026-04-01T12:00:00",
            "games": [{"id": "g1", "status": "ready", "version": "1"}],
        }
    )

    assert download_calls == []


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
        request_set_all_games_ready=lambda: None,
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    # Initialize startup gate baseline.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T13:00:00",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True
    assert download_calls == []

    # Gate confirms on newer stable snapshot; recovery should run in this same call.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T13:00:01",
            "games": [{"id": "g1", "status": "ready", "version": "1"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False
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
        request_set_all_games_ready=lambda: None,
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
    applier = RemoteDeviceConfigApplier(deps, rt)

    # No parseable timestamps in startup snapshot.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "games": [{"id": "g1", "status": "playing", "version": "1"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is True
    assert download_calls == []

    # A stable snapshot without timestamps should still confirm gate and recover.
    applier.apply(
        {
            "last_update_source": "supabase_bridge",
            "games": [{"id": "g1", "status": "ready", "version": "1"}],
        }
    )
    assert rt.awaiting_games_ready_confirmation is False
    assert download_calls == [("g1", "1")]

