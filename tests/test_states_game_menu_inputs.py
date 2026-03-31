import time

from states.game import GameSelectState, InGameState
from states.menu import MenuState


class _Proc:
    def __init__(self):
        self.signals = []

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals.append(sig)


class _Ctx:
    def __init__(self):
        self.game_list = [{"id": "g1", "preview": [bytearray(128 * 128 * 3)]}]
        self.game_index = 0
        self.game_preview_index = 0
        self.page_tick = time.time()
        self.game = None
        self.pages = []
        self.current_button_state = {}
        self.transitions = []
        self.reload_conf = False
        self.trigger_dim_check = False
        self.status_calls = []
        self.proc = _Proc()

        self.term_game_process = lambda _g: None
        self.term_widget_processes = lambda _p: None
        self.start_game_process = lambda _gid: {"process": self.proc, "game_id": "g1"}
        self.set_game_status = lambda gid, st: self.status_calls.append((gid, st))
        self.get_device_info = lambda: {"model": "PixelDart"}

    def transition_to(self, state):
        self.transitions.append(type(state).__name__)


def test_game_select_input_wrap_and_start_transition():
    ctx = _Ctx()
    state = GameSelectState()

    state.handle_input(ctx, {"btn_left": True})
    assert ctx.game_index == 0

    state.handle_input(ctx, {"btn_right": True})
    assert ctx.game_index == 0

    state.handle_input(ctx, {"btn_a": True})
    assert ctx.transitions[-1] == "InGameState"
    assert ctx.status_calls[-1] == ("g1", "playing")


def test_ingame_home_opens_overlay_and_overlay_b_ends_game():
    ctx = _Ctx()
    state = InGameState()
    ctx.game = {"process": ctx.proc, "game_id": "g1"}

    # HOME on PixelDart pauses process and opens pause overlay.
    state.handle_input(ctx, {"btn_home": True})
    assert state.is_showing_exit_game_overlay(ctx) is True
    assert ctx.proc.signals, "expected SIGSTOP signal"

    # B in overlay ends game and returns to menu.
    state.handle_input(ctx, {"btn_b": True})
    assert ctx.game is None
    assert ctx.transitions[-1] == "MenuState"
    assert ctx.status_calls[-1] == ("g1", "ready")


def test_menu_input_wrap_and_home_transition():
    ctx = _Ctx()
    ctx.menu_select_index = 0
    ctx.pages = [{"uuid": "x"}]
    state = MenuState()

    state.handle_input(ctx, {"btn_left": True})
    assert ctx.menu_select_index == 2
    state.handle_input(ctx, {"btn_right": True})
    assert ctx.menu_select_index == 0

    state.handle_input(ctx, {"btn_home": True})
    assert ctx.transitions[-1] == "WidgetState"
