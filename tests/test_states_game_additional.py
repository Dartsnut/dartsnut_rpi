import signal

from PIL import Image

import states.game as sgame
from states.game import GameSelectState, InGameState


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


class _Shm:
    def __init__(self):
        self.buf = bytearray(1 + 128 * 160 * 3)


class _Ctx:
    def __init__(self):
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
        self.display = _Display()

        self.term_game_process = lambda _g: None
        self.term_widget_processes = lambda _p: None
        self.start_game_process = lambda _gid: {"process": self.proc, "game_id": "g1"}
        self.set_game_status = lambda gid, st: self.status_calls.append((gid, st))
        self.get_device_info = lambda: {}

    def transition_to(self, state):
        self.transitions.append(type(state).__name__)


def test_empty_game_list_uses_dynamic_bluetooth_qr(monkeypatch):
    ctx = _Ctx()
    ctx.game_list = []
    qr_surface = Image.new("RGB", (128, 128), "black")
    calls = []
    monkeypatch.setattr(
        sgame,
        "create_bluetooth_qr_for_device",
        lambda device_info: calls.append(device_info) or qr_surface,
    )

    state = GameSelectState()
    state.update(ctx)
    state.update(ctx)

    assert calls == [{}]
    assert ctx.display.frames == [qr_surface, qr_surface]


def test_ingame_home_on_pixelboard_goes_to_widget(monkeypatch):
    import states.game as sgame

    monkeypatch.setattr(sgame, "is_pixelboard_device", lambda: True)
    ctx = _Ctx()
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


def test_standard_game_loading_clears_on_first_frame_and_does_not_reappear(monkeypatch):
    ctx = _Ctx()
    state = InGameState()
    shm = _Shm()
    loading_image = Image.new("RGB", (128, 160), "yellow")
    monkeypatch.setattr(sgame.assets, "create_loading_image", lambda: loading_image)
    ctx.game = {
        "process": ctx.proc,
        "game_id": "g1",
        "shm": shm,
        "loading": True,
    }

    shm.buf[0] = 1
    state.update(ctx)

    assert ctx.game["loading"] is True
    assert ctx.display.frames == [loading_image]

    first_frame = Image.new("RGB", (128, 160), "red")
    shm.buf[1:] = first_frame.tobytes()
    shm.buf[0] = 0
    state.update(ctx)

    assert ctx.game["loading"] is False
    assert shm.buf[0] == 1
    assert ctx.display.frames[-1].getpixel((0, 0)) == (255, 0, 0)

    frame_count = len(ctx.display.frames)
    state.update(ctx)

    assert len(ctx.display.frames) == frame_count
