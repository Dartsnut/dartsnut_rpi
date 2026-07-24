from __future__ import annotations

from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

import runtime.bluetooth_qr as bluetooth_qr
import states.settings as ssettings
from states.settings import SettingsState, _controller_display_label


class _Display:
    def __init__(self):
        self.frame = None

    def update_frame_buffer(self, frame):
        self.frame = frame


class _BluetoothController:
    def __init__(self, state=None):
        self.state = state or {
            "is_scan": False,
            "controllers": [],
            "scan_results": [],
        }
        self.refresh_calls = 0
        self.scan_calls = 0
        self.connect_calls = []

    def get_state_snapshot(self):
        return {
            "is_scan": bool(self.state.get("is_scan")),
            "controllers": [dict(row) for row in self.state.get("controllers", [])],
            "scan_results": [dict(row) for row in self.state.get("scan_results", [])],
        }

    def refresh_remembered_if_requested(self):
        self.refresh_calls += 1
        return True

    def start_scan_if_requested(self, sync_connected_controllers=False):
        self.scan_calls += 1
        if self.state.get("is_scan"):
            return False
        self.state["is_scan"] = True
        self.state["scan_results"] = []
        return True

    def start_connect_if_requested(self, address, source_list="scan_results"):
        self.connect_calls.append((address, source_list))
        return True


def _open_connectivity(ctx, state):
    ctx.setting_select_index = 5
    state.handle_input(ctx, {"btn_a": True})


def _open_controllers(ctx, state):
    _open_connectivity(ctx, state)
    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_a": True})


class _Ctx:
    def __init__(self):
        self.transitions = []
        self.setting_select_index = 3
        self._device_info = {"brightness": "79", "volume": "90", "model": "PixelDart"}
        self.reset_called = 0
        self.brightness_calls = []
        self.volume_calls = []
        self.display = _Display()
        self.bluetooth_controller = _BluetoothController()
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

    _open_connectivity(ctx, state)
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

    _open_connectivity(ctx, state)
    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_home": True})

    assert state.consumes_btn_b_for_overlay(ctx) is False
    assert ctx.transitions == []


def test_settings_bluetooth_unavailable_still_opens_dismissible_overlay(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    monkeypatch.setattr(ssettings, "resolve_bluetooth_local_name", lambda device_info: None)

    _open_connectivity(ctx, state)
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


def test_settings_connectivity_and_controller_navigation():
    ctx = _Ctx()
    state = SettingsState()

    _open_connectivity(ctx, state)
    assert state._page_mode == "connectivity"

    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_a": True})
    assert state._page_mode == "controllers"
    assert ctx.bluetooth_controller.refresh_calls == 1

    state.handle_input(ctx, {"btn_b": True})
    assert state._page_mode == "connectivity"
    state.handle_input(ctx, {"btn_b": True})
    assert state._page_mode == "settings"
    assert ctx.setting_select_index == 5


def test_settings_home_from_submenu_goes_to_main_menu():
    ctx = _Ctx()
    state = SettingsState()
    _open_controllers(ctx, state)

    state.handle_input(ctx, {"btn_home": True})

    assert ctx.transitions[-1] == "MenuState"


def test_controller_rows_order_and_dedupe():
    ctx = _Ctx()
    state = SettingsState()
    snapshot = {
        "is_scan": False,
        "controllers": [
            {"name": "Remembered", "mac": "AA:BB", "status": "connected"}
        ],
        "scan_results": [
            {"name": "Duplicate", "mac": "aa:bb", "status": "idle"},
            {"name": "Found", "mac": "CC:DD", "status": "idle"},
            {"name": "Found dup", "mac": "cc:dd", "status": "idle"},
        ],
    }

    rows = state._controller_rows(snapshot)

    assert [row["key"] for row in rows] == [
        "controller:AA:BB",
        "scan",
        "result:CC:DD",
    ]


def test_controller_scan_clears_results_and_ignores_a_while_scanning():
    ctx = _Ctx()
    ctx.bluetooth_controller.state["scan_results"] = [
        {"name": "Old", "mac": "AA:BB", "status": "idle"}
    ]
    state = SettingsState()
    _open_controllers(ctx, state)

    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_a": True})

    assert ctx.bluetooth_controller.scan_calls == 1
    assert ctx.bluetooth_controller.state["scan_results"] == []


def test_controller_connects_result_then_selects_promoted_remembered_row():
    ctx = _Ctx()
    ctx.bluetooth_controller.state["scan_results"] = [
        {"name": "Pad", "mac": "AA:BB", "status": "idle"}
    ]
    state = SettingsState()
    _open_controllers(ctx, state)
    state.handle_input(ctx, {"btn_down": True})

    state.handle_input(ctx, {"btn_a": True})

    assert ctx.bluetooth_controller.connect_calls == [("AA:BB", "scan_results")]
    assert state._controller_selected_key == "controller:AA:BB"


def test_controller_retries_error_remembered_and_ignores_connected():
    ctx = _Ctx()
    ctx.bluetooth_controller.state["controllers"] = [
        {"name": "Bad", "mac": "AA:BB", "status": "error"},
        {"name": "Good", "mac": "CC:DD", "status": "connected"},
    ]
    state = SettingsState()
    _open_controllers(ctx, state)
    state._controller_selected_key = "controller:AA:BB"

    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_a": True})

    assert ctx.bluetooth_controller.connect_calls == [("AA:BB", "controllers")]


def test_controller_selection_scrolls_and_preserves_stable_key():
    ctx = _Ctx()
    ctx.bluetooth_controller.state["scan_results"] = [
        {"name": f"Pad {index}", "mac": f"AA:{index:02d}", "status": "idle"}
        for index in range(9)
    ]
    state = SettingsState()
    _open_controllers(ctx, state)

    for _ in range(8):
        state.handle_input(ctx, {"btn_down": True})
    selected_key = state._controller_selected_key
    state.update(ctx)

    assert selected_key == "result:AA:07"
    assert state._controller_selected_key == selected_key
    assert ctx.display.frame.size == (128, 160)


def test_controller_render_shows_connected_error_and_activity_colors():
    ctx = _Ctx()
    ctx.bluetooth_controller.state = {
        "is_scan": True,
        "controllers": [
            {"name": "Good", "mac": "AA:BB", "status": "connected"},
            {"name": "Bad", "mac": "CC:DD", "status": "error"},
            {"name": "Wait", "mac": "EE:FF", "status": "connecting"},
        ],
        "scan_results": [],
    }
    state = SettingsState()
    _open_controllers(ctx, state)

    state.update(ctx)

    colors = {color for _count, color in ctx.display.frame.getcolors(maxcolors=65536)}
    assert (0, 255, 0) in colors
    assert (255, 0, 0) in colors
    assert (255, 101, 140) in colors


def test_controller_display_label_truncates_and_keeps_mac_suffix():
    label = _controller_display_label(
        "An Extremely Long Controller Name", "AA:BB:CC:DD:EE:FF"
    )

    assert len(label) <= 18
    assert label.endswith("EE:FF")
    assert "..." in label


def test_bluetooth_qr_payload_uses_deep_link_and_url_encodes_name():
    assert bluetooth_qr.bluetooth_qr_payload("PixelDart-272c") == (
        "dartsnut://device/connect?ble_name=PixelDart-272c"
    )
    assert bluetooth_qr.bluetooth_qr_payload("Pixel Dart/ä") == (
        "dartsnut://device/connect?ble_name=Pixel%20Dart%2F%C3%A4"
    )


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

    monkeypatch.setattr(bluetooth_qr.qrcode, "QRCode", _FakeQr)

    surface = bluetooth_qr.create_bluetooth_qr_surface("PixelDart-eeff")

    assert captured["payload"] == "dartsnut://device/connect?ble_name=PixelDart-eeff"
    assert captured["fit"] is True
    assert (
        captured["kwargs"]["error_correction"]
        == bluetooth_qr.qrcode.constants.ERROR_CORRECT_Q
    )
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
    assert bluetooth_qr._BLUETOOTH_QR_LOGO.size == (45, 45)
    assert min(
        bluetooth_qr._BLUETOOTH_QR_LOGO.getpixel((x, y))[3]
        for y in range(9, 36)
        for x in range(9, 36)
    ) == 255


def test_bluetooth_qr_surface_uses_real_qrcode_backend():
    surface = bluetooth_qr.create_bluetooth_qr_surface("PixelDart-eeff")

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
    qr_surface = Image.new("RGB", (128, 128), (255, 0, 0))

    monkeypatch.setattr(ssettings, "get_primary_ipv4", lambda: "127.0.0.1")
    monkeypatch.setattr(
        ssettings,
        "resolve_bluetooth_local_name",
        lambda device_info: "PixelDart-eeff",
    )
    monkeypatch.setattr(ssettings, "_create_bluetooth_qr_surface", lambda payload: qr_surface)

    _open_connectivity(ctx, state)
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
