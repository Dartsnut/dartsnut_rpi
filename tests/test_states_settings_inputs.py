from __future__ import annotations

from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

import states.settings as ssettings
from states.settings import SettingsState


class _Display:
    def __init__(self):
        self.frame = None

    def update_frame_buffer(self, frame):
        self.frame = frame


class _Ctx:
    def __init__(self):
        self.transitions = []
        self.setting_select_index = 3
        self._device_info = {"brightness": "79", "volume": "90", "model": "PixelDart"}
        self.reset_called = 0
        self.brightness_calls = []
        self.volume_calls = []
        self.display = _Display()
        default_font = ImageFont.load_default()
        self.assets = SimpleNamespace(
            font8=default_font,
            font_6x8=default_font,
            font24=default_font,
            wifi_icon=Image.new("RGBA", (8, 8), (255, 255, 255, 255)),
            settings_icon=Image.new("RGBA", (16, 16), (255, 255, 255, 255)),
        )

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
    ctx.setting_select_index = 6

    state.handle_input(ctx, {"btn_a": True})
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_a": True})
    assert ctx.reset_called == 1
    assert state.consumes_btn_b_for_overlay(ctx) is False


def test_settings_confirm_overlay_btn_b_or_home_cancels_without_reset():
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 6

    state.handle_input(ctx, {"btn_a": True})
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_b": True})
    assert ctx.reset_called == 0
    assert state.consumes_btn_b_for_overlay(ctx) is False

    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_home": True})
    assert ctx.reset_called == 0
    assert state.consumes_btn_b_for_overlay(ctx) is False


def test_settings_bluetooth_qr_uses_local_name_and_btn_b_dismisses(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 5
    qr_surface = Image.new("RGB", (128, 128), (255, 0, 0))
    captured = []

    monkeypatch.setattr(
        ssettings,
        "resolve_bluetooth_local_name",
        lambda device_info: "PixelDart-eeff",
    )
    monkeypatch.setattr(
        ssettings,
        "_create_bluetooth_qr_surface",
        lambda payload: captured.append(payload) or qr_surface,
    )

    state.handle_input(ctx, {"btn_a": True})
    assert captured == ["PixelDart-eeff"]
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_left": True, "btn_a": True})
    assert ctx.brightness_calls == []
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_b": True})
    assert state.consumes_btn_b_for_overlay(ctx) is False
    assert ctx.transitions == []


def test_settings_bluetooth_qr_home_dismisses(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 5
    monkeypatch.setattr(
        ssettings,
        "resolve_bluetooth_local_name",
        lambda device_info: "PixelDart-eeff",
    )
    monkeypatch.setattr(
        ssettings,
        "_create_bluetooth_qr_surface",
        lambda payload: Image.new("RGB", (128, 128), "white"),
    )

    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_home": True})

    assert state.consumes_btn_b_for_overlay(ctx) is False
    assert ctx.transitions == []


def test_settings_bluetooth_unavailable_still_opens_dismissible_overlay(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 5
    monkeypatch.setattr(ssettings, "resolve_bluetooth_local_name", lambda device_info: None)

    state.handle_input(ctx, {"btn_a": True})

    assert state.consumes_btn_b_for_overlay(ctx) is True
    assert state._bluetooth_qr_surface is None
    state.handle_input(ctx, {"btn_b": True})
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

    ctx.setting_select_index = 3
    state.handle_input(ctx, {"btn_left": True})
    assert ctx.brightness_calls[-1] == 69

    ctx._device_info["brightness"] = "100"
    state.handle_input(ctx, {"btn_right": True})
    assert ctx.brightness_calls[-1] == 95

    ctx.setting_select_index = 4
    ctx._device_info["volume"] = "90"
    state.handle_input(ctx, {"btn_left": True})
    assert ctx.volume_calls[-1] == 80

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

    ctx.setting_select_index = 6
    state.handle_input(ctx, {"btn_down": True})
    assert ctx.setting_select_index == 6

    ctx.setting_select_index = 5
    state.handle_input(ctx, {"btn_down": True})
    assert ctx.setting_select_index == 6


def test_bluetooth_qr_surface_encodes_payload_and_is_centered(monkeypatch):
    captured = {}

    class _FakeQr:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def add_data(self, payload):
            captured["payload"] = payload

        def make(self, fit):
            captured["fit"] = fit

        def make_image(self, **kwargs):
            captured["image_kwargs"] = kwargs
            image = Image.new("1", (25, 25), 0)
            ImageDraw.Draw(image).rectangle((8, 8, 16, 16), fill=1)
            return image

    monkeypatch.setattr(ssettings.qrcode, "QRCode", _FakeQr)

    surface = ssettings._create_bluetooth_qr_surface("PixelDart-eeff")

    assert captured["payload"] == "PixelDart-eeff"
    assert captured["fit"] is True
    assert captured["kwargs"]["border"] == 2
    assert captured["image_kwargs"] == {
        "fill_color": "white",
        "back_color": "black",
    }
    assert surface.mode == "RGB"
    assert surface.size == (128, 128)
    assert surface.getpixel((0, 0)) == (0, 0, 0)
    assert surface.getpixel((45, 45)) == (255, 255, 255)
    assert surface.getpixel((64, 64)) == (255, 255, 255)
    assert surface.getpixel((55, 55)) not in ((0, 0, 0), (255, 255, 255))
    assert ssettings._BLUETOOTH_QR_LOGO.size == (35, 35)
    assert min(
        ssettings._BLUETOOTH_QR_LOGO.getpixel((x, y))[3]
        for y in range(7, 28)
        for x in range(7, 28)
    ) == 255


def test_bluetooth_qr_surface_uses_real_qrcode_backend():
    surface = ssettings._create_bluetooth_qr_surface("PixelDart-eeff")

    assert surface.mode == "RGB"
    assert surface.size == (128, 128)
    assert surface.getpixel((0, 0)) == (0, 0, 0)
    assert any(
        surface.getpixel((x, y)) == (255, 255, 255)
        for y in range(surface.height)
        for x in range(surface.width)
    )
    assert surface.getpixel((64, 64)) == (255, 255, 255)
    assert surface.getpixel((55, 55)) not in ((0, 0, 0), (255, 255, 255))


def test_bluetooth_qr_render_replaces_only_main_surface(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    ctx.setting_select_index = 5
    qr_surface = Image.new("RGB", (128, 128), (255, 0, 0))

    monkeypatch.setattr(ssettings, "get_primary_ipv4", lambda: "127.0.0.1")
    monkeypatch.setattr(
        ssettings,
        "resolve_bluetooth_local_name",
        lambda device_info: "PixelDart-eeff",
    )
    monkeypatch.setattr(ssettings, "_create_bluetooth_qr_surface", lambda payload: qr_surface)

    state.handle_input(ctx, {"btn_a": True})
    state.update(ctx)

    frame = ctx.display.frame
    assert frame.size == (128, 160)
    assert frame.crop((0, 0, 128, 128)).tobytes() == qr_surface.tobytes()
    footer = frame.crop((0, 128, 128, 160))
    assert all(
        footer.getpixel((x, y)) != (255, 0, 0)
        for y in range(footer.height)
        for x in range(footer.width)
    )
