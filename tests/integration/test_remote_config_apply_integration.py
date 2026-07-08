from __future__ import annotations

import json
from pathlib import Path

import pytest

from game_lifecycle import load_menu_game_list, refresh_menu_game_list_if_requested
from runtime.remote_device_config import (
    RemoteConfigRuntimeState,
    RemoteDeviceConfigApplier,
    RemoteDeviceConfigDependencies,
)


@pytest.mark.integration
# Multi-field remote config application
def test_remote_config_applies_multi_field_snapshot(remote_config_harness, app_ctx):
    app_ctx.reload_pages = False
    apply = remote_config_harness["apply"]
    machine_state = remote_config_harness["machine_state"]
    events = remote_config_harness["events"]

    apply(
        {
            "last_update_source": "supabase_bridge",
            "pages_updated_at": "2026-04-22T10:00:00",
            "pages": [{"uuid": "p1", "widgets": []}],
            "brightness": 77,
            "volume": 31,
            "time_zone": "Asia/Taipei",
            "dim_window": {
                "dim_window_enabled": True,
                "dim_window_start": "22:00",
                "dim_window_end": "06:00",
                "dim_level": 10,
                "dim_restore_seconds": 30,
            },
            "device_info": {"name": "BasementBoard"},
        }
    )

    assert machine_state.pages == [{"uuid": "p1", "widgets": []}]
    assert machine_state.brightness == 77
    assert machine_state.volume == 31
    assert machine_state.device_name == "BasementBoard"
    assert machine_state.dim_window["dim_window_enabled"] is True
    assert events["tz"] == "Asia/Taipei"
    assert app_ctx.reload_pages is True


@pytest.mark.integration
def test_external_change_from_supabase_inbound_config_updates_local_machine(
    remote_config_harness,
):
    apply = remote_config_harness["apply"]
    state = remote_config_harness["machine_state"]
    events = remote_config_harness["events"]
    apply(
        {
            "brightness": 81,
            "volume": 66,
            "dim_window": {
                "dim_window_enabled": True,
                "dim_window_start": "22:00",
                "dim_window_end": "06:00",
                "dim_level": 15,
                "dim_restore_seconds": 30,
            },
            "device_info": {"name": "LivingRoom"},
        }
    )

    assert events["reload_called"] is True
    assert state.brightness == 81
    assert state.volume == 66
    assert state.device_name == "LivingRoom"
    assert state.dim_window["dim_window_enabled"] is True


@pytest.mark.integration
# Game command and lifecycle application
def test_remote_config_game_playing_sets_start_game(remote_config_harness, app_ctx):
    app_ctx.start_game = False
    app_ctx.game_id = None
    runtime = remote_config_harness["runtime"]
    runtime.startup_firmware_version = None
    runtime.startup_settlement_completed = True
    runtime.awaiting_games_ready_confirmation = False
    remote_config_harness["apply"](
        {
            "games": [{"id": "g1", "status": "playing", "version": ""}],
            "device_updated_at": "2026-03-30T00:00:01",
        }
    )

    assert app_ctx.start_game is True
    assert app_ctx.game_id == "g1"


@pytest.mark.integration
def test_remote_config_game_install_requests_download_then_ready(remote_config_harness):
    runtime = remote_config_harness["runtime"]
    runtime.startup_firmware_version = None
    runtime.startup_settlement_completed = True
    runtime.awaiting_games_ready_confirmation = False
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]

    apply(
        {
            "games": [{"id": "g-install", "status": "downloading", "version": "1.2.3"}],
            "device_updated_at": "2026-03-30T00:00:01",
        }
    )

    assert events["ensure_download_calls"] == [("g-install", "1.2.3")]
    assert events["status_updates"] == [("g-install", "downloading"), ("g-install", "ready")]


@pytest.mark.integration
def test_remote_config_game_ready_terminates_running_game(remote_config_harness, app_ctx):
    runtime = remote_config_harness["runtime"]
    runtime.startup_firmware_version = None
    runtime.startup_settlement_completed = True
    runtime.awaiting_games_ready_confirmation = False
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    app_ctx.game = {"game_id": "g-remove"}
    app_ctx.reload_conf = False

    apply(
        {
            "games": [{"id": "g-remove", "status": "ready", "version": "1.0.0"}],
            "device_updated_at": "2026-03-30T00:00:02",
        }
    )

    assert events["term_calls"] == 1
    assert events["status_updates"] == [("g-remove", "ready")]
    assert app_ctx.game is None
    assert app_ctx.reload_conf is True


@pytest.mark.integration
def test_remote_config_game_removed_during_download_cancels_and_no_readd(remote_config_harness):
    runtime = remote_config_harness["runtime"]
    runtime.startup_firmware_version = None
    runtime.startup_settlement_completed = True
    runtime.awaiting_games_ready_confirmation = False
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    events["ensure_download_result"] = False

    apply(
        {
            "games": [{"id": "g-cancel", "status": "downloading", "version": "2.0.0"}],
            "device_updated_at": "2026-03-30T00:00:01",
        }
    )
    apply(
        {
            "games": [],
            "device_updated_at": "2026-03-30T00:00:02",
        }
    )

    assert events["ensure_download_calls"] == [("g-cancel", "2.0.0")]
    assert events["cancel_download_calls"] == ["g-cancel"]
    assert events["status_updates"] == [("g-cancel", "downloading")]
    assert runtime.remote_downloading_game_ids == set()


@pytest.mark.integration
# Firmware update application
def test_remote_config_firmware_update_publishes_completion_and_persists(remote_config_harness):
    runtime = remote_config_harness["runtime"]
    runtime.startup_firmware_version = None
    apply = remote_config_harness["apply"]
    machine_state = remote_config_harness["machine_state"]
    events = remote_config_harness["events"]
    call_order = []
    remote_config_harness["deps"].publish_partial_state = lambda p: call_order.append(
        ("publish", dict(p))
    ) or events["published"].append(dict(p))

    def _perform_update(before_terminal_action=None):
        call_order.append(("perform_update", None))
        assert not events["published"]
        before_terminal_action()
        call_order.append(("terminal", None))
        return {"error": False}

    remote_config_harness["deps"].perform_update = _perform_update

    apply({"firmware": {"update": True}})

    assert call_order[:3] == [
        ("perform_update", None),
        ("publish", {"firmware": {"update": False}}),
        ("terminal", None),
    ]
    assert {"firmware": {"version": "9.9.9", "update": False}} in events["published"]
    assert machine_state.firmware_info == {"version": "9.9.9", "update": False}
    assert runtime.firmware_update_in_progress is False


@pytest.mark.integration
def test_remote_config_firmware_update_clears_flag_on_update_failure(remote_config_harness):
    runtime = remote_config_harness["runtime"]
    runtime.startup_firmware_version = None
    apply = remote_config_harness["apply"]
    machine_state = remote_config_harness["machine_state"]
    events = remote_config_harness["events"]
    call_order = []
    remote_config_harness["deps"].publish_partial_state = lambda p: call_order.append(
        ("publish", dict(p))
    ) or events["published"].append(dict(p))

    def _perform_update(before_terminal_action=None):
        call_order.append(("perform_update", None))
        assert not events["published"]
        before_terminal_action()
        return {"error": True}

    remote_config_harness["deps"].perform_update = _perform_update

    apply({"firmware": {"update": True}})

    assert call_order == [
        ("perform_update", None),
        ("publish", {"firmware": {"update": False}}),
    ]
    assert {"firmware": {"version": "9.9.9", "update": False}} not in events["published"]
    assert machine_state.firmware_info == {"version": "", "update": False}
    assert runtime.firmware_update_in_progress is False


@pytest.mark.integration
def test_remote_apply_then_refresh_syncs_menu_game_list(
    workspace, app_ctx, machine_state, monkeypatch
):
    """Mirror bridge order: apply remote config (games) then soft reload refresh."""
    monkeypatch.setattr(
        "game_lifecycle._load_user_data",
        lambda: {"game_playtimes": {}},
    )
    for gid, name in (("g1", "B"), ("g2", "A")):
        d = Path("apps") / gid
        d.mkdir(parents=True, exist_ok=True)
        (d / "conf.json").write_text(
            json.dumps(
                {"id": gid, "name": name, "type": "game", "version": "1.0.0"}
            ),
            encoding="utf-8",
        )

    events: dict = {
        "published": [],
        "status_updates": [],
        "ensure_download_calls": [],
        "cancel_download_calls": [],
        "remove_local_game_calls": [],
        "term_calls": 0,
    }
    ble = type(
        "FakeBle",
        (),
        {
            "start_scan_if_requested": lambda self: None,
            "start_connect_if_requested": lambda self, _a: None,
        },
    )()

    def _ensure_game_downloaded(gid, ver):
        events["ensure_download_calls"].append((gid, ver))
        d = Path("apps") / gid
        d.mkdir(parents=True, exist_ok=True)
        (d / "conf.json").write_text(
            json.dumps(
                {"id": gid, "name": gid.upper(), "type": "game", "version": ver or "1.0.0"}
            ),
            encoding="utf-8",
        )
        return True

    def _remove_local_game(gid):
        from game_lifecycle import remove_local_game_folder

        events["remove_local_game_calls"].append(gid)
        return remove_local_game_folder(gid)

    deps = RemoteDeviceConfigDependencies(
        app_ctx=app_ctx,
        get_machine_state_service=lambda: machine_state,
        bluetooth_scan_controller=ble,
        publish_partial_state=lambda p: events["published"].append(dict(p)),
        request_set_game_status=lambda gid, st: events["status_updates"].append((gid, st)),
        set_time_zone=lambda _tz: None,
        term_game_process=lambda _g: events.__setitem__("term_calls", events["term_calls"] + 1),
        ensure_game_downloaded=_ensure_game_downloaded,
        cancel_game_download=lambda gid: events["cancel_download_calls"].append(gid),
        local_game_version_matches=lambda *_a: False,
        remove_local_game_folder=_remove_local_game,
        perform_update=lambda: {"error": False},
        get_version=lambda: {"error": False, "version": "9.9.9"},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: None,
    )
    runtime = RemoteConfigRuntimeState(startup_firmware_version=None)
    runtime.startup_settlement_completed = True
    runtime.awaiting_games_ready_confirmation = False
    applier = RemoteDeviceConfigApplier(deps, runtime)

    app_ctx.load_game_list = lambda: load_menu_game_list(app_ctx)
    app_ctx.game_list = [{"id": "stale"}]

    applier.apply({"games": [{"id": "g1", "status": "ready", "version": ""}]})
    refresh_menu_game_list_if_requested(app_ctx)
    assert [g["id"] for g in app_ctx.game_list] == ["g1"]
    assert events["remove_local_game_calls"] == ["g2"]
    assert app_ctx.reload_game_menu is False

    app_ctx.game_index = 1
    applier.apply({"games": [{"id": "g1", "status": "ready", "version": ""}]})
    refresh_menu_game_list_if_requested(app_ctx)
    assert app_ctx.game_index == 0

    applier.apply(
        {
            "games": [
                {"id": "g1", "status": "ready", "version": ""},
                {"id": "g2", "status": "ready", "version": ""},
            ]
        }
    )
    refresh_menu_game_list_if_requested(app_ctx)
    assert {g["id"] for g in app_ctx.game_list} == {"g1", "g2"}
    assert events["ensure_download_calls"] == [("g2", "")]
