from game_firestore_sync import handle_incoming_game_status
from states.game import InGameState


def test_handle_download_status_sets_downloading_then_ready():
    events = []

    handle_incoming_game_status(
        "chess",
        "download",
        current_game_id="",
        game_exists=lambda _gid: False,
        ensure_game_downloaded=lambda _gid: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda _gid: events.append(("launch", "called")),
        terminate_running_game=lambda _gid: events.append(("terminate", "called")),
    )

    assert events == [("chess", "downloading"), ("chess", "ready")]


def test_handle_playing_status_downloads_then_requests_launch_when_missing():
    events = []

    handle_incoming_game_status(
        "chess",
        "playing",
        current_game_id="",
        game_exists=lambda _gid: False,
        ensure_game_downloaded=lambda _gid: True,
        set_game_status=lambda gid, status: events.append((gid, status)),
        request_launch=lambda gid: events.append(("launch", gid)),
        terminate_running_game=lambda _gid: events.append(("terminate", "called")),
    )

    assert events == [("chess", "downloading"), ("launch", "chess")]


def test_handle_ready_status_terminates_matching_running_game():
    events = []

    handle_incoming_game_status(
        "chess",
        "ready",
        current_game_id="chess",
        game_exists=lambda _gid: True,
        ensure_game_downloaded=lambda _gid: True,
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
