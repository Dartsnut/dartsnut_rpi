from __future__ import annotations

import time

from states.settings import SettingsState
from states.widget import WidgetState


class _Ctx:
    def __init__(self):
        self.transitions = []
        self.setting_select_index = 3
        self._device_info = {"brightness": "79", "volume": "90", "model": "PixelDart"}
        self.reset_called = 0
        self.brightness_calls = []
        self.volume_calls = []

    def transition_to(self, state):
        self.transitions.append(type(state).__name__)

    def get_device_info(self):
        return dict(self._device_info)

    def reset_device(self):
        self.reset_called += 1

    def set_brightness(self, raw_brightness):
        self.brightness_calls.append(raw_brightness)

    def set_volume(self, raw_volume):
        self.volume_calls.append(raw_volume)


def test_settings_confirm_overlay_btn_a_resets_and_clears():
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 5

    # Enter confirm overlay
    state.handle_input(ctx, {"btn_a": True})
    assert state.consumes_btn_b_for_overlay(ctx) is True

    # Confirm -> calls reset_device and clears overlay
    state.handle_input(ctx, {"btn_a": True})
    assert ctx.reset_called == 1
    assert state.consumes_btn_b_for_overlay(ctx) is False


def test_settings_confirm_overlay_btn_b_or_home_cancels_without_reset():
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 5

    state.handle_input(ctx, {"btn_a": True})
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_b": True})
    assert ctx.reset_called == 0
    assert state.consumes_btn_b_for_overlay(ctx) is False

    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_home": True})
    assert ctx.reset_called == 0
    assert state.consumes_btn_b_for_overlay(ctx) is False


def test_settings_btn_b_or_home_from_main_goes_to_menu():
    ctx = _Ctx()
    state = SettingsState()
    state.handle_input(ctx, {"btn_b": True})
    assert ctx.transitions[-1] == "MenuState"

    ctx.transitions.clear()
    state2 = SettingsState()
    state2.handle_input(ctx, {"btn_home": True})
    assert ctx.transitions[-1] == "MenuState"


def test_settings_brightness_and_volume_adjustments_left_right():
    ctx = _Ctx()
    state = SettingsState()

    # idx=3 brightness dec: raw 79 -> level 8 -> dec -> 7 -> raw 73
    ctx.setting_select_index = 3
    state.handle_input(ctx, {"btn_left": True})
    assert ctx.brightness_calls[-1] == 73

    # idx=3 brightness inc: raw 100 already maps to max level -> stay at canonical max 97
    ctx._device_info["brightness"] = "100"
    state.handle_input(ctx, {"btn_right": True})
    assert ctx.brightness_calls[-1] == 97

    # idx=4 volume dec: raw 90 -> level 9 -> dec -> 8 -> raw 80
    ctx.setting_select_index = 4
    ctx._device_info["volume"] = "90"
    state.handle_input(ctx, {"btn_left": True})
    assert ctx.volume_calls[-1] == 80

    # idx=4 volume inc: raw 100 -> raw 100
    ctx._device_info["volume"] = "100"
    state.handle_input(ctx, {"btn_right": True})
    assert ctx.volume_calls[-1] == 100


def test_settings_up_down_clamps_index():
    ctx = _Ctx()
    state = SettingsState()

    ctx.setting_select_index = 3
    state.handle_input(ctx, {"btn_up": True})
    assert ctx.setting_select_index == 3

    ctx.setting_select_index = 4
    state.handle_input(ctx, {"btn_up": True})
    assert ctx.setting_select_index == 3

    ctx.setting_select_index = 5
    state.handle_input(ctx, {"btn_down": True})
    assert ctx.setting_select_index == 5

    ctx.setting_select_index = 4
    state.handle_input(ctx, {"btn_down": True})
    assert ctx.setting_select_index == 5

