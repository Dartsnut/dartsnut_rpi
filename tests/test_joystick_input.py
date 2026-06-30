from runtime.joystick_input import should_consume_joystick_button


class _State:
    def __init__(self, name="in_game", showing_overlay=False):
        self._name = name
        self._showing_overlay = showing_overlay

    def name(self):
        return self._name

    def is_showing_exit_game_overlay(self, _ctx):
        return self._showing_overlay


class _Ctx:
    def __init__(self, state_name="in_game", game_id="g1", showing_overlay=False):
        self.current_state = _State(state_name, showing_overlay)
        self.game = {"game_id": game_id}


def test_pico8_ingame_without_overlay_does_not_consume_joystick_home():
    ctx = _Ctx(game_id="pico8")

    assert should_consume_joystick_button(ctx, "btn_home") is False


def test_non_pico8_ingame_without_overlay_consumes_joystick_home():
    ctx = _Ctx(game_id="chess")

    assert should_consume_joystick_button(ctx, "btn_home") is True


def test_pico8_ingame_overlay_consumes_joystick_buttons():
    ctx = _Ctx(game_id="pico8", showing_overlay=True)

    assert should_consume_joystick_button(ctx, "btn_a") is True
    assert should_consume_joystick_button(ctx, "btn_b") is True
    assert should_consume_joystick_button(ctx, "btn_home") is True


def test_non_game_state_consumes_joystick_home():
    ctx = _Ctx(state_name="menu", game_id="pico8")

    assert should_consume_joystick_button(ctx, "btn_home") is True
