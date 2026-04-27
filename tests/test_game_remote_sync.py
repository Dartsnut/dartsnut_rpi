from domain.game_remote_sync import handle_incoming_game_status, are_remote_playing_games_cleared
from states.game import InGameState


def test_are_remote_playing_games_cleared_ignores_downloading_entries():
    assert are_remote_playing_games_cleared(
        [
            {"id": "chess", "status": "downloading"},
            {"id": "pong", "status": "ready"},
        ]
    )


def test_are_remote_playing_games_cleared_false_when_any_playing():
    assert not are_remote_playing_games_cleared(
        [
            {"id": "chess", "status": "ready"},
            {"id": "pong", "status": "playing"},
        ]
    )


def test_handle_downloading_status_sets_downloading_then_ready():
    events = []

    handle_incoming_game_status(
        "chess",
        "downloading",
        expected_version="",
        current_game_id="",
        game_exists=lambda _gid: False,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda _gid: events.append(("launch", "called")),
        terminate_running_game=lambda _gid: events.append(("terminate", "called")),
    )

    assert events == [("chess", "downloading"), ("chess", "ready")]


def test_handle_downloading_passes_expected_version_to_downloader():
    calls = []

    handle_incoming_game_status(
        "chess",
        "downloading",
        expected_version="2.0.0",
        current_game_id="",
        game_exists=lambda _gid: False,
        ensure_game_downloaded=lambda gid, ver: calls.append((gid, ver)) or True,
        set_game_status=lambda _gid, _status: None,
        request_launch=lambda _gid: None,
        terminate_running_game=lambda _gid: None,
    )

    assert calls == [("chess", "2.0.0")]


def test_handle_download_status_is_ignored():
    events = []

    handle_incoming_game_status(
        "chess",
        "download",
        expected_version="",
        current_game_id="",
        game_exists=lambda _gid: False,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda _gid: events.append(("launch", "called")),
        terminate_running_game=lambda _gid: events.append(("terminate", "called")),
    )

    assert events == []


def test_handle_playing_status_requests_launch_when_game_already_exists():
    events = []

    handle_incoming_game_status(
        "chess",
        "playing",
        expected_version="",
        current_game_id="",
        game_exists=lambda _gid: True,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda gid: events.append(("launch", gid)),
        terminate_running_game=lambda _gid: events.append(("terminate", "called")),
    )

    assert events == [("launch", "chess")]


def test_handle_playing_status_downloads_then_requests_launch_when_missing():
    events = []

    handle_incoming_game_status(
        "chess",
        "playing",
        expected_version="",
        current_game_id="",
        game_exists=lambda _gid: False,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda gid: events.append(("launch", gid)),
        terminate_running_game=lambda _gid: events.append(("terminate", "called")),
    )

    assert events == [("chess", "downloading"), ("launch", "chess")]


def test_handle_playing_status_switches_running_game_before_launch():
    events = []

    handle_incoming_game_status(
        "pong",
        "playing",
        expected_version="",
        current_game_id="chess",
        game_exists=lambda _gid: True,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda gid: events.append(("launch", gid)),
        terminate_running_game=lambda gid: events.append(("terminate", gid)),
    )

    assert events == [("terminate", "chess"), ("chess", "ready"), ("launch", "pong")]


def test_handle_playing_status_same_running_game_is_noop():
    events = []

    handle_incoming_game_status(
        "chess",
        "playing",
        expected_version="",
        current_game_id="chess",
        game_exists=lambda _gid: True,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda gid: events.append(("launch", gid)),
        terminate_running_game=lambda gid: events.append(("terminate", gid)),
    )

    assert events == []


def test_handle_ready_status_terminates_matching_running_game():
    events = []

    handle_incoming_game_status(
        "chess",
        "ready",
        expected_version="",
        current_game_id="chess",
        game_exists=lambda _gid: True,
        ensure_game_downloaded=lambda _gid, _ver: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda _gid: events.append(("launch", "called")),
        terminate_running_game=lambda gid: events.append(("terminate", gid)),
    )

    assert events == [("terminate", "chess"), ("chess", "ready")]


class _ProcessDone:
    def poll(self):
        return 1


class _ProcessRunning:
    def poll(self):
        return None


class _Ctx:
    def __init__(self):
        self.game = None
        self.reload_conf = False
        self.status_events = []
        self.transitioned_to = None
        self.current_button_state = {}
        self.trigger_dim_check = False
        self.set_game_status = lambda gid, status: self.status_events.append((gid, status))
        self.term_game_process = lambda _game: None

    def transition_to(self, state):
        self.transitioned_to = state.name()

    def get_device_info(self):
        return {"model": "PixelDart"}


def test_ingame_update_marks_ready_when_process_terminated():
    state = InGameState()
    ctx = _Ctx()
    ctx.game = {"game_id": "chess", "process": _ProcessDone()}

    state.update(ctx)

    assert ctx.reload_conf is True
    assert ("chess", "ready") in ctx.status_events


def test_ingame_overlay_end_marks_ready_on_manual_exit():
    state = InGameState()
    ctx = _Ctx()
    ctx.game = {"game_id": "chess", "process": _ProcessRunning()}
    state._showing_pause_overlay = True
    state.handle_input(ctx, {"btn_b": True})

    assert ("chess", "ready") in ctx.status_events
    assert ctx.transitioned_to == "menu"
