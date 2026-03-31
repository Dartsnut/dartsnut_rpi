from __future__ import annotations

from states.game import InGameState


# Local button-driven publish flow
def test_button_start_game_publishes_playing_status(game_sim_harness):
    ctx = game_sim_harness["ctx"]
    state = game_sim_harness["select_state"]
    transitions = game_sim_harness["transitions"]
    published = game_sim_harness["remote_sync"].published
    widget_terminated = game_sim_harness["widget_terminated"]

    state.handle_input(ctx, {"btn_a": True})

    assert transitions[-1] == "in_game"
    assert widget_terminated == [True]
    assert published[-1] == {"games": [{"id": "chess", "status": "playing"}]}


def test_button_home_then_b_ends_game_and_publishes_ready(game_sim_harness):
    ctx = game_sim_harness["ctx"]
    process = game_sim_harness["process"]
    published = game_sim_harness["remote_sync"].published
    transitions = game_sim_harness["transitions"]
    game_terminated = game_sim_harness["game_terminated"]
    state = InGameState()
    ctx.game = {
        "process": process,
        "shm": None,
        "game_id": "chess",
        "launched": True,
        "pico8_first_frame_seen": False,
    }

    state.handle_input(ctx, {"btn_home": True})
    assert state.is_showing_exit_game_overlay(ctx) is True
    assert process.signals, "expected game process to receive SIGSTOP on HOME"

    state.handle_input(ctx, {"btn_b": True})

    assert ctx.game is None
    assert game_terminated == ["chess"]
    assert transitions[-1] == "menu"
    assert published[-1] == {"games": [{"id": "chess", "status": "ready"}]}


# Inbound remote game command handling
def test_inbound_playing_status_requests_local_launch(remote_config_harness):
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    runtime = remote_config_harness["runtime"]
    app_ctx = remote_config_harness["app_ctx"]

    runtime.startup_games_reset_initialized = True
    runtime.awaiting_games_ready_confirmation = False
    runtime.startup_filter_playing_until_newer_update = False

    apply({"games": [{"id": "chess", "status": "playing", "version": "1.0.0"}]})

    assert app_ctx.start_game is True
    assert app_ctx.game_id == "chess"
    assert ("chess", "downloading") in events["status_updates"]
    assert events["reload_called"] is True


def test_inbound_ready_status_terminates_running_game_and_publishes(remote_config_harness):
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    app_ctx = remote_config_harness["app_ctx"]
    runtime = remote_config_harness["runtime"]
    runtime.startup_games_reset_initialized = True
    runtime.awaiting_games_ready_confirmation = False
    app_ctx.game = {"game_id": "chess", "process": object(), "shm": None}

    apply({"games": [{"id": "chess", "status": "ready", "version": "1.0.0"}]})

    assert app_ctx.game is None
    assert events["term_calls"] == 1
    assert ("chess", "ready") in events["status_updates"]
    assert events["reload_called"] is True


def test_inbound_downloading_with_matching_local_version_publishes_ready(remote_config_harness):
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    deps = remote_config_harness["deps"]
    runtime = remote_config_harness["runtime"]
    runtime.startup_games_reset_initialized = True
    runtime.awaiting_games_ready_confirmation = False

    deps.local_game_version_matches = lambda gid, ver: gid == "chess" and ver == "2.0.0"
    apply({"games": [{"id": "chess", "status": "downloading", "version": "2.0.0"}]})

    assert ("chess", "ready") in events["status_updates"]
    assert events["ensure_download_calls"] == []


# Startup gate behavior
def test_startup_games_ready_retry_then_newer_playing_is_applied(remote_config_harness):
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    runtime = remote_config_harness["runtime"]
    app_ctx = remote_config_harness["app_ctx"]

    runtime.awaiting_games_ready_confirmation = True
    apply(
        {
            "updated_at": "2026-03-30T10:00:00",
            "games": [{"id": "chess", "status": "playing", "version": "1.0.0"}],
        }
    )
    assert events["all_ready_requests"] == 1
    assert app_ctx.start_game is False

    apply(
        {
            "updated_at": "2026-03-30T10:00:00",
            "games": [{"id": "chess", "status": "ready", "version": "1.0.0"}],
        }
    )
    assert runtime.awaiting_games_ready_confirmation is True
    assert runtime.startup_filter_playing_until_newer_update is False

    apply(
        {
            "updated_at": "2026-03-30T10:00:01",
            "games": [{"id": "chess", "status": "ready", "version": "1.0.0"}],
        }
    )
    assert runtime.awaiting_games_ready_confirmation is False
    assert runtime.startup_filter_playing_until_newer_update is True

    apply(
        {
            "updated_at": "2026-03-30T10:00:00",
            "games": [{"id": "chess", "status": "playing", "version": "1.0.0"}],
        }
    )
    assert app_ctx.start_game is False

    apply(
        {
            "updated_at": "2026-03-30T10:00:02",
            "games": [{"id": "chess", "status": "playing", "version": "1.0.0"}],
        }
    )
    assert app_ctx.start_game is True
    assert app_ctx.game_id == "chess"


def test_startup_pending_blocks_playing_until_all_ready_with_newer_timestamp(remote_config_harness):
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    runtime = remote_config_harness["runtime"]
    app_ctx = remote_config_harness["app_ctx"]

    runtime.awaiting_games_ready_confirmation = True
    runtime.startup_filter_playing_until_newer_update = False

    # Initial snapshot: stale playing should trigger retry and stay blocked.
    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:00",
            "games": [{"id": "chess", "status": "playing", "version": "1.0.0"}],
        }
    )
    assert events["all_ready_requests"] == 1
    assert app_ctx.start_game is False

    # Same timestamp + all ready should still keep pending.
    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:00",
            "games": [{"id": "chess", "status": "ready", "version": "1.0.0"}],
        }
    )
    assert runtime.awaiting_games_ready_confirmation is True
    assert app_ctx.start_game is False

    # Newer timestamp + all ready should confirm pending reset.
    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:01",
            "games": [{"id": "chess", "status": "ready", "version": "1.0.0"}],
        }
    )
    assert runtime.awaiting_games_ready_confirmation is False

    # Once confirmed, newer playing command is accepted.
    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-03-31T10:00:02",
            "games": [{"id": "chess", "status": "playing", "version": "1.0.0"}],
        }
    )
    assert app_ctx.start_game is True
    assert app_ctx.game_id == "chess"


def test_startup_pending_keeps_downloading_untouched_and_defers_download_until_gate_pass(
    remote_config_harness,
):
    apply = remote_config_harness["apply"]
    events = remote_config_harness["events"]
    runtime = remote_config_harness["runtime"]

    runtime.awaiting_games_ready_confirmation = True
    runtime.startup_filter_playing_until_newer_update = False

    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T12:00:00",
            "games": [
                {"id": "chess", "status": "playing", "version": "1.0.0"},
            ],
        }
    )
    assert events["all_ready_requests"] == 1

    # Newer timestamp and only ready/downloading statuses should pass the gate.
    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T12:00:01",
            "games": [
                {"id": "chess", "status": "downloading", "version": "2.0.0"},
                {"id": "pong", "status": "ready", "version": "1.1.0"},
            ],
        }
    )
    assert runtime.awaiting_games_ready_confirmation is False
    # Download handling is deferred for the transition frame.
    assert events["ensure_download_calls"] == []
    assert events["status_updates"] == []

    # After gate passes, normal downloading flow applies on subsequent updates.
    apply(
        {
            "last_update_source": "supabase_bridge",
            "updated_at": "2026-04-01T12:00:02",
            "games": [
                {"id": "chess", "status": "downloading", "version": "2.0.0"},
                {"id": "pong", "status": "ready", "version": "1.1.0"},
            ],
        }
    )
    assert events["ensure_download_calls"] == [("chess", "2.0.0")]
    assert ("chess", "downloading") in events["status_updates"]
