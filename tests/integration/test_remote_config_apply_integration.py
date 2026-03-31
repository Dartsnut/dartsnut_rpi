from __future__ import annotations

import pytest


@pytest.mark.integration
# Multi-field remote config application
def test_remote_config_applies_multi_field_snapshot(remote_config_harness, app_ctx):
    app_ctx.reload_pages = False
    apply = remote_config_harness["apply"]
    machine_state = remote_config_harness["machine_state"]
    events = remote_config_harness["events"]

    apply(
        {
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
    runtime.startup_games_reset_initialized = True
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
    runtime.startup_games_reset_initialized = True
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
    runtime.startup_games_reset_initialized = True
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
    runtime.startup_games_reset_initialized = True
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

    apply({"firmware": {"update": True}})

    assert {"firmware": {"version": "9.9.9", "update": False}} in events["published"]
    assert machine_state.firmware_info == {"version": "9.9.9", "update": False}
    assert runtime.firmware_update_in_progress is False
