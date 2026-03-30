from __future__ import annotations

import pytest


@pytest.mark.integration
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
def test_remote_config_game_playing_sets_start_game(remote_config_harness, app_ctx):
    app_ctx.start_game = False
    app_ctx.game_id = None
    remote_config_harness["runtime"].startup_firmware_version = None
    remote_config_harness["apply"](
        {
            "games": [{"id": "g1", "status": "playing", "version": ""}],
            "device_updated_at": "2026-03-30T00:00:01",
        }
    )

    assert app_ctx.start_game is True
    assert app_ctx.game_id == "g1"


@pytest.mark.integration
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
