"""Characterization tests for Firestore-shaped remote config application."""

import json
import os
from unittest.mock import MagicMock

from domain.app_context import AppContext
from runtime.remote_device_config import (
    RemoteConfigRuntimeState,
    RemoteDeviceConfigApplier,
    RemoteDeviceConfigDependencies,
    is_firestore_reset_confirmed,
    parse_iso_ts,
)


def test_parse_iso_ts_accepts_z_suffix():
    dt = parse_iso_ts("2026-03-25T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 3 and dt.day == 25


def test_is_firestore_reset_confirmed_requires_empty_network_and_dim_disabled():
    assert is_firestore_reset_confirmed(
        {
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    )
    assert not is_firestore_reset_confirmed(
        {
            "ip_address": "1.2.3.4",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": False},
        }
    )


def test_apply_sets_pages_and_reload_flag_without_calling_game_logic(
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
    assert ctx.reload_pages is True


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
    rt.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, rt)

    applier.apply(
        {
            "games": [{"id": "g1", "status": "playing", "version": ""}],
        }
    )

    assert game_ctx.start_game is True
    assert game_ctx.game_id == "g1"
