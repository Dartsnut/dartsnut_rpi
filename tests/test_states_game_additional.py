import signal

from states.game import InGameState


class _Proc:
    def __init__(self):
        self.signals = []

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals.append(sig)


class _Display:
    def __init__(self):
        self.frames = []

    def update_frame_buffer(self, frame):
        self.frames.append(frame)


class _ClosedShm:
    buf = None


class _Ctx:
    def __init__(self, model="PixelDart"):
        self.game_list = [{"id": "g1", "preview": [bytearray(128 * 128 * 3)]}]
        self.game_index = 0
        self.game_preview_index = 0
        self.page_tick = 0
        self.game = None
        self.pages = []
        self.current_button_state = {}
        self.transitions = []
        self.reload_conf = False
        self.trigger_dim_check = False
        self.status_calls = []
        self.proc = _Proc()
        self.model = model
        self.display = _Display()

        self.term_game_process = lambda _g: None
        self.term_widget_processes = lambda _p: None
        self.start_game_process = lambda _gid: {"process": self.proc, "game_id": "g1"}
        self.set_game_status = lambda gid, st: self.status_calls.append((gid, st))
        self.get_device_info = lambda: {"model": self.model}

    def transition_to(self, state):
        self.transitions.append(type(state).__name__)


def test_ingame_home_on_pixelboard_goes_to_widget():
    ctx = _Ctx(model="PixelBoard")
    state = InGameState()
    ctx.game = {"process": ctx.proc, "game_id": "g1"}

    state.handle_input(ctx, {"btn_home": True})

    assert ctx.reload_conf is True
    assert ctx.trigger_dim_check is True
    assert ctx.transitions[-1] == "WidgetState"


def test_ingame_overlay_resume_requires_a_release():
    ctx = _Ctx()
    state = InGameState()
    ctx.game = {"process": ctx.proc, "game_id": "g1"}
    state._showing_pause_overlay = True

    state.handle_input(ctx, {"btn_a": True})
    assert state.is_showing_exit_game_overlay(ctx) is True

    ctx.current_button_state = {"btn_a": False}
    state.handle_input(ctx, {})

    assert state.is_showing_exit_game_overlay(ctx) is False
    assert signal.SIGCONT in ctx.proc.signals


def test_ingame_update_handles_closed_shared_memory_buffer():
    ctx = _Ctx()
    state = InGameState()
    ctx.game = {"process": ctx.proc, "game_id": "g1", "shm": _ClosedShm()}

    state.update(ctx)

    assert len(ctx.display.frames) == 1
