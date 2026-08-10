from __future__ import annotations

from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

import runtime.bluetooth_qr as bluetooth_qr
import states.settings as ssettings
from states.settings import (
    SettingsState,
    _controller_display_label,
    _controller_marquee_label,
)


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


class _WifiController:
    def __init__(self):
        self.state = {
            "is_scan": False,
            "networks": [],
            "connected_network": None,
            "remembered_networks": [],
            "scan_error": "",
            "connection_status": "idle",
            "connection_ssid": "",
            "connection_profile": "",
            "connection_error": "",
            "forget_status": "idle",
            "forget_profile": "",
            "forget_ssid": "",
            "forget_error": "",
        }
        self.scan_calls = 0
        self.connect_calls = []
        self.saved_connect_calls = []
        self.forget_calls = []
        self.clear_calls = 0
        self.clear_forget_calls = 0

    def get_state_snapshot(self):
        return {
            **self.state,
            "networks": [dict(row) for row in self.state.get("networks", [])],
            "remembered_networks": [
                dict(row) for row in self.state.get("remembered_networks", [])
            ],
        }

    def start_scan_if_requested(self):
        self.scan_calls += 1
        if self.state["is_scan"]:
            return False
        self.state["is_scan"] = True
        return True

    def start_connect_if_requested(self, ssid, password, secured):
        self.connect_calls.append((ssid, password, secured))
        self.state.update(
            connection_status="connecting",
            connection_ssid=ssid,
            connection_error="",
        )
        return True

    def clear_connection_result(self):
        self.clear_calls += 1
        if self.state["connection_status"] != "connecting":
            self.state.update(
                connection_status="idle", connection_ssid="",
                connection_profile="", connection_error=""
            )

    def start_connect_saved_if_requested(self, profile, ssid):
        self.saved_connect_calls.append((profile, ssid))
        self.state.update(
            connection_status="connecting", connection_ssid=ssid,
            connection_profile=profile, connection_error=""
        )
        return True

    def start_forget_if_requested(self, profile, ssid):
        self.forget_calls.append((profile, ssid))
        self.state.update(
            forget_status="forgetting", forget_profile=profile,
            forget_ssid=ssid, forget_error=""
        )
        return True

    def clear_forget_result(self):
        self.clear_forget_calls += 1
        if self.state["forget_status"] != "forgetting":
            self.state.update(
                forget_status="idle", forget_profile="", forget_ssid="",
                forget_error=""
            )


def _open_connectivity(ctx, state):
    ctx.setting_select_index = 5
    state.handle_input(ctx, {"btn_a": True})


def _open_display(ctx, state):
    ctx.setting_select_index = 4
    state.handle_input(ctx, {"btn_a": True})


def test_settings_display_submenu_and_brightness_level():
    ctx = _Ctx()
    ctx.set_brightness_level = lambda level: ctx.brightness_calls.append(level)
    state = SettingsState()

    _open_display(ctx, state)
    state.handle_input(ctx, {"btn_right": True})

    assert ctx.brightness_calls == [9]
    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_a": True})
    assert state.consumes_btn_b_for_overlay(ctx) is True
    state.handle_input(ctx, {"btn_b": True})
    assert state.consumes_btn_b_for_overlay(ctx) is False


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
        self.wifi_controller = _WifiController()
        default_font = ImageFont.load_default()
        self.assets = SimpleNamespace(
            font8=default_font,
            font_6x8=default_font,
            font24=default_font,
            system_font10=default_font,
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
        "_create_connection_qr_surface",
        lambda local_name, **kwargs: captured.append((local_name, kwargs)) or qr_surface,
    )

    _open_connectivity(ctx, state)
    state.handle_input(ctx, {"btn_a": True})
    assert captured == [("PixelDart-eeff", {"supabase_connected": False, "device_id": ""})]
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_left": True, "btn_a": True})
    assert ctx.brightness_calls == []
    assert state.consumes_btn_b_for_overlay(ctx) is True

    state.handle_input(ctx, {"btn_b": True})
    assert state.consumes_btn_b_for_overlay(ctx) is False
    assert ctx.transitions == []


def test_settings_bluetooth_qr_refreshes_when_supabase_connects(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    sync_state = {"connected": False}
    captured = []

    class _Sync:
        def is_connected(self):
            return sync_state["connected"]

    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _Sync())
    monkeypatch.setattr(
        ssettings, "resolve_bluetooth_local_name", lambda _info: "PixelDart-eeff"
    )
    monkeypatch.setattr(
        ssettings,
        "resolve_bluetooth_device_id",
        lambda _info: "AA:BB:CC:DD:EE:FF",
    )
    monkeypatch.setattr(
        ssettings,
        "_create_connection_qr_surface",
        lambda local_name, **kwargs: captured.append((local_name, kwargs))
        or Image.new("RGB", (128, 128), "white"),
    )

    _open_connectivity(ctx, state)
    state.handle_input(ctx, {"btn_a": True})
    sync_state["connected"] = True
    state.update(ctx)

    assert captured == [
        (
            "PixelDart-eeff",
            {
                "supabase_connected": False,
                "device_id": "AA:BB:CC:DD:EE:FF",
            },
        ),
        (
            "PixelDart-eeff",
            {
                "supabase_connected": True,
                "device_id": "AA:BB:CC:DD:EE:FF",
            },
        ),
    ]


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
        "_create_connection_qr_surface",
        lambda local_name, **kwargs: Image.new("RGB", (128, 128), "white"),
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


def test_connecting_status_uses_four_dot_activity_indicator(monkeypatch):
    state = SettingsState()
    image = Image.new("RGB", (24, 24), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    monkeypatch.setattr(ssettings.time, "time", lambda: 0.0)

    state._draw_controller_status_icon(draw, "connecting", 12, 12)

    assert image.getpixel((12, 8)) == (255, 101, 140)
    assert image.getpixel((16, 12)) == (96, 96, 96)
    assert image.getpixel((12, 16)) == (96, 96, 96)
    assert image.getpixel((8, 12)) == (96, 96, 96)


def test_controller_status_icon_is_a_small_colored_dot():
    state = SettingsState()
    image = Image.new("RGB", (20, 20), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    state._draw_controller_status_icon(draw, "connected", 10, 10)

    green_pixels = [
        (x, y)
        for y in range(20)
        for x in range(20)
        if image.getpixel((x, y)) == (0, 255, 0)
    ]
    assert green_pixels
    assert all(8 <= x <= 12 and 8 <= y <= 12 for x, y in green_pixels)
    assert len(green_pixels) <= 25


def test_controller_display_label_truncates_without_mac_suffix():
    label = _controller_display_label("An Extremely Long Controller Name")

    assert label == "An Extremely Lo..."
    assert ":" not in label


def test_controller_display_label_uses_plain_fallback_for_missing_name():
    assert _controller_display_label("") == "Controller"


def test_controller_marquee_pauses_then_scrolls_and_wraps():
    name = "DUALSHOCK 4 Wireless Controller"

    assert _controller_marquee_label(name, 0.9) == name[:18]
    assert _controller_marquee_label(name, 1.25) == name[1:19]
    wrapped = _controller_marquee_label(name, 1.0 + len(name) * 0.25)
    assert wrapped.startswith("   DUALSHOCK")
    assert len(wrapped) == 18


def test_controller_marquee_resets_when_selected_row_changes(monkeypatch):
    state = SettingsState()
    first = {"key": "controller:AA", "name": "First Very Long Controller"}
    second = {"key": "controller:BB", "name": "Second Very Long Controller"}
    now = [10.0]
    monkeypatch.setattr(ssettings.time, "time", lambda: now[0])

    assert state._controller_row_label(first, True) == first["name"][:18]
    now[0] = 11.25
    assert state._controller_row_label(first, True) == first["name"][1:19]
    assert state._controller_row_label(second, True) == second["name"][:18]


def test_connection_qr_payload_uses_bind_when_supabase_connected():
    assert bluetooth_qr.connection_qr_payload(
        "PixelDart-eeff",
        supabase_connected=True,
        device_id="AA:BB/CC",
    ) == "dartsnut://device/bind?device_id=AA%3ABB%2FCC"


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
    monkeypatch.setattr(ssettings, "_create_connection_qr_surface", lambda local_name, **kwargs: qr_surface)

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


def _open_wifi(ctx, state):
    _open_connectivity(ctx, state)
    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_a": True})


def test_settings_wifi_navigation_starts_scan_and_returns_to_connectivity():
    ctx = _Ctx()
    state = SettingsState()

    _open_wifi(ctx, state)

    assert state._page_mode == "wifi"
    assert ctx.wifi_controller.scan_calls == 1

    state.handle_input(ctx, {"btn_b": True})
    assert state._page_mode == "connectivity"
    assert state._connectivity_select_index == 2


def test_wifi_password_editor_cycles_moves_trims_and_connects():
    ctx = _Ctx()
    ctx.wifi_controller.state["networks"] = [
        {"ssid": "家庭网络", "rssi": 90, "security": "WPA2", "secured": True}
    ]
    state = SettingsState()
    _open_wifi(ctx, state)
    ctx.wifi_controller.state["is_scan"] = False

    state.handle_input(ctx, {"btn_down": True})
    state.handle_input(ctx, {"btn_a": True})

    assert state._page_mode == "wifi_password"
    assert state._wifi_cursor_index == 0
    assert state._wifi_password == [" "] * ssettings.WIFI_PASSWORD_LENGTH

    state.handle_input(ctx, {"btn_up": True})
    state.handle_input(ctx, {"btn_right": True})
    state.handle_input(ctx, {"btn_up": True})
    state.handle_input(ctx, {"btn_up": True})
    state.handle_input(ctx, {"btn_a": True})

    assert ctx.wifi_controller.connect_calls == [("家庭网络", "AB", True)]


def test_wifi_password_character_order_prioritizes_common_input():
    characters = ssettings.WIFI_PASSWORD_CHARACTERS
    expected_prefix = (
        " "
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        + ssettings.WIFI_PASSWORD_FREQUENT_SPECIALS
    )

    assert characters.startswith(expected_prefix)
    assert len(characters) == 95
    assert len(set(characters)) == 95
    assert set(characters) == {chr(code) for code in range(32, 127)}


def test_wifi_password_character_wrap_and_cursor_clamp():
    ctx = _Ctx()
    state = SettingsState()
    state._enter_wifi_password(
        {"ssid": "Open", "rssi": 50, "security": "--", "secured": False}
    )

    state.handle_input(ctx, {"btn_down": True})
    assert state._wifi_password[0] == "~"
    state.handle_input(ctx, {"btn_up": True})
    assert state._wifi_password[0] == " "

    state.handle_input(ctx, {"btn_left": True})
    assert state._wifi_cursor_index == 0
    state._wifi_cursor_index = ssettings.WIFI_PASSWORD_LENGTH - 1
    state.handle_input(ctx, {"btn_right": True})
    assert state._wifi_cursor_index == ssettings.WIFI_PASSWORD_LENGTH - 1


def test_wifi_open_network_connects_with_blank_password():
    ctx = _Ctx()
    state = SettingsState()
    state._enter_wifi_password(
        {"ssid": "Open", "rssi": 50, "security": "--", "secured": False}
    )

    state.handle_input(ctx, {"btn_a": True})

    assert ctx.wifi_controller.connect_calls == [("Open", "", False)]


def test_wifi_connect_success_returns_to_list_and_rescans():
    ctx = _Ctx()
    state = SettingsState()
    state._enter_wifi_password(
        {"ssid": "Home", "rssi": 80, "security": "WPA2", "secured": True}
    )
    ctx.wifi_controller.state.update(
        connection_status="success", connection_ssid="Home"
    )

    state.update(ctx)

    assert state._page_mode == "wifi"
    assert ctx.wifi_controller.clear_calls == 1
    assert ctx.wifi_controller.scan_calls == 1


def test_wifi_connect_error_stays_on_password_screen_and_renders_unicode():
    ctx = _Ctx()
    state = SettingsState()
    state._enter_wifi_password(
        {"ssid": "家庭网络", "rssi": 80, "security": "WPA2", "secured": True}
    )
    ctx.wifi_controller.state.update(
        connection_status="error",
        connection_ssid="家庭网络",
        connection_error="Incorrect password",
    )

    state.update(ctx)

    assert state._page_mode == "wifi_password"
    assert ctx.display.frame.size == (128, 160)


def test_wifi_password_window_fits_variable_width_system_font():
    ctx = _Ctx()
    state = SettingsState()
    state._wifi_password = ["W"] * ssettings.WIFI_PASSWORD_LENGTH
    state._wifi_cursor_index = ssettings.WIFI_PASSWORD_LENGTH // 2
    image = Image.new("RGB", (128, 160), "black")
    draw = ImageDraw.Draw(image)

    start, visible = state._wifi_password_window(
        draw, ctx.assets.system_font10
    )

    assert start <= state._wifi_cursor_index < start + len(visible)
    assert draw.textlength(visible, font=ctx.assets.system_font10) <= 118


def test_wifi_selecting_new_network_clears_stale_connection_result():
    ctx = _Ctx()
    ctx.wifi_controller.state.update(
        networks=[
            {"ssid": "First", "rssi": 90, "security": "WPA2", "secured": True},
            {"ssid": "Second", "rssi": 80, "security": "WPA2", "secured": True},
        ],
        connection_status="error",
        connection_ssid="First",
        connection_error="Incorrect password",
    )
    state = SettingsState()
    state._page_mode = "wifi"
    state._wifi_selected_index = 2
    state._wifi_selected_key = "network:Second"

    state.handle_input(ctx, {"btn_a": True})

    assert state._page_mode == "wifi_password"
    assert state._wifi_selected_network["ssid"] == "Second"
    assert ctx.wifi_controller.clear_calls == 1
    assert ctx.wifi_controller.state["connection_status"] == "idle"


def test_wifi_rendering_disables_text_antialiasing(monkeypatch):
    ctx = _Ctx()
    ctx.wifi_controller.state["networks"] = [
        {"ssid": "Home", "rssi": 80, "security": "WPA2", "secured": True}
    ]
    state = SettingsState()
    created_draws = []
    original_draw = ssettings.ImageDraw.Draw

    def capture_draw(*args, **kwargs):
        draw = original_draw(*args, **kwargs)
        created_draws.append(draw)
        return draw

    monkeypatch.setattr(ssettings.ImageDraw, "Draw", capture_draw)

    state._render_wifi(ctx)
    state._enter_wifi_password(ctx.wifi_controller.state["networks"][0])
    state._render_wifi_password(ctx)

    assert created_draws
    assert all(draw.fontmode == "1" for draw in created_draws)


def test_wifi_signal_levels_and_colors_follow_three_tier_mapping():
    state = SettingsState()

    assert state._wifi_signal_level(100) == 3
    assert state._wifi_signal_level(67) == 3
    assert state._wifi_signal_level(66) == 2
    assert state._wifi_signal_level(34) == 2
    assert state._wifi_signal_level(33) == 1
    assert state._wifi_signal_level(None) == 1

    image = Image.new("RGB", (128, 20), "black")
    draw = ImageDraw.Draw(image)
    state._draw_wifi_signal_dots(draw, 80, 124, 10)
    assert image.getpixel((110, 10)) == ssettings.WIFI_SIGNAL_COLORS[3]
    assert image.getpixel((117, 10)) == ssettings.WIFI_SIGNAL_COLORS[3]
    assert image.getpixel((124, 10)) == ssettings.WIFI_SIGNAL_COLORS[3]

    image = Image.new("RGB", (128, 20), "black")
    draw = ImageDraw.Draw(image)
    state._draw_wifi_signal_dots(draw, 50, 124, 10)
    assert image.getpixel((117, 10)) == ssettings.WIFI_SIGNAL_COLORS[2]
    assert image.getpixel((124, 10)) == ssettings.WIFI_SIGNAL_COLORS[2]

    image = Image.new("RGB", (128, 20), "black")
    draw = ImageDraw.Draw(image)
    state._draw_wifi_signal_dots(draw, 20, 124, 10)
    assert image.getpixel((124, 10)) == ssettings.WIFI_SIGNAL_COLORS[1]


def test_wifi_long_selected_ssid_scrolls_and_resets_for_new_row(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    font = ctx.assets.system_font10
    row = {"key": "network:first", "ssid": "First Very Long WiFi Network Name"}
    now = [10.0]
    monkeypatch.setattr(ssettings.time, "time", lambda: now[0])

    first = Image.new("RGB", (128, 18), "black")
    state._draw_wifi_ssid_label(
        first, row, True, font, (0, 0, 0), 0, max_width=60
    )
    assert state._wifi_scroll_key == "network:first"
    assert state._wifi_scroll_started_at == 10.0

    now[0] = 11.25
    scrolled = Image.new("RGB", (128, 18), "black")
    state._draw_wifi_ssid_label(
        scrolled, row, True, font, (0, 0, 0), 0, max_width=60
    )
    assert first.crop((2, 0, 62, 18)).tobytes() != scrolled.crop(
        (2, 0, 62, 18)
    ).tobytes()

    next_row = {"key": "network:second", "ssid": "Second Long WiFi Network Name"}
    state._draw_wifi_ssid_label(
        Image.new("RGB", (128, 18), "black"),
        next_row,
        True,
        font,
        (0, 0, 0),
        0,
        max_width=60,
    )
    assert state._wifi_scroll_key == "network:second"
    assert state._wifi_scroll_started_at == 11.25


def test_wifi_long_unfocused_ssid_remains_static(monkeypatch):
    ctx = _Ctx()
    state = SettingsState()
    row = {"key": "network:long", "ssid": "A Very Long WiFi Network Name"}
    now = [10.0]
    monkeypatch.setattr(ssettings.time, "time", lambda: now[0])

    first = Image.new("RGB", (128, 18), "black")
    state._draw_wifi_ssid_label(
        first, row, False, ctx.assets.system_font10, (255, 255, 255), 0, 60
    )
    now[0] = 20.0
    second = Image.new("RGB", (128, 18), "black")
    state._draw_wifi_ssid_label(
        second, row, False, ctx.assets.system_font10, (255, 255, 255), 0, 60
    )

    assert first.tobytes() == second.tobytes()
    assert state._wifi_scroll_key is None


def test_wifi_rows_show_current_above_rescan_and_filter_duplicate():
    state = SettingsState()
    rows = state._wifi_rows(
        {
            "connected_network": {
                "ssid": "Home",
                "rssi": 88,
                "security": "WPA2",
                "secured": True,
                "connected": True,
            },
            "networks": [
                {"ssid": "Home", "rssi": 88, "secured": True},
                {"ssid": "Guest", "rssi": 70, "secured": False},
            ],
        }
    )

    assert [row["type"] for row in rows] == ["current", "rescan", "network"]
    assert [row.get("ssid") for row in rows] == ["Home", None, "Guest"]


def test_wifi_rows_put_current_and_remembered_above_rescan():
    state = SettingsState()
    rows = state._wifi_rows(
        {
            "connected_network": {
                "ssid": "Home", "profile": "home-profile",
                "rssi": 88, "connected": True,
            },
            "remembered_networks": [
                {"ssid": "Office", "profile": "office-profile", "rssi": 70}
            ],
            "networks": [
                {"ssid": "Home", "rssi": 88},
                {"ssid": "Office", "rssi": 70},
                {"ssid": "Guest", "rssi": 60},
            ],
        }
    )

    assert [row["type"] for row in rows] == [
        "current", "remembered", "rescan", "network"
    ]
    assert [row.get("ssid") for row in rows] == [
        "Home", "Office", None, "Guest"
    ]


def test_wifi_remembered_network_opens_connecting_screen_without_input():
    ctx = _Ctx()
    ctx.wifi_controller.state["remembered_networks"] = [
        {"ssid": "Office", "profile": "office-profile", "rssi": 70}
    ]
    state = SettingsState()
    state._page_mode = "wifi"
    state._wifi_selected_key = "remembered:office-profile"

    state.handle_input(ctx, {"btn_a": True})
    state.update(ctx)

    assert state._page_mode == "wifi_password"
    assert state._wifi_connection_uses_saved_profile is True
    assert ctx.wifi_controller.saved_connect_calls == [("office-profile", "Office")]
    frame = ctx.display.frame
    # Password input outline would have white pixels at these corners.
    assert frame.getpixel((1, 43)) == (0, 0, 0)
    assert frame.getpixel((126, 65)) == (0, 0, 0)


def test_wifi_connected_network_asks_then_forgets_on_confirm():
    ctx = _Ctx()
    ctx.wifi_controller.state["connected_network"] = {
        "ssid": "Home", "profile": "home-profile", "rssi": 88,
        "connected": True,
    }
    state = SettingsState()
    state._page_mode = "wifi"
    state._wifi_selected_key = "current:Home"

    state.handle_input(ctx, {"btn_a": True})

    assert state._overlay_mode == "wifi_forget_confirm"
    assert ctx.wifi_controller.forget_calls == []

    state.handle_input(ctx, {"btn_a": True})

    assert ctx.wifi_controller.forget_calls == [("home-profile", "Home")]
    assert state._overlay_mode == "wifi_forget_confirm"

    ctx.wifi_controller.state["forget_status"] = "success"
    state.update(ctx)

    assert state._overlay_mode is None
    assert ctx.wifi_controller.clear_forget_calls == 1
    assert ctx.wifi_controller.scan_calls == 1


def test_wifi_connected_forget_confirmation_can_be_cancelled():
    ctx = _Ctx()
    ctx.wifi_controller.state["connected_network"] = {
        "ssid": "Home", "profile": "home-profile", "rssi": 88,
        "connected": True,
    }
    state = SettingsState()
    state._page_mode = "wifi"
    state._wifi_selected_key = "current:Home"

    state.handle_input(ctx, {"btn_a": True})
    state.handle_input(ctx, {"btn_b": True})

    assert state._overlay_mode is None
    assert ctx.wifi_controller.forget_calls == []


def test_wifi_password_up_hold_repeats_after_delay(monkeypatch):
    ctx = _Ctx()
    ctx.current_button_state = {"btn_up": True}
    state = SettingsState()
    state._enter_wifi_password(
        {"ssid": "Home", "rssi": 80, "security": "WPA2", "secured": True}
    )
    now = [10.0]
    monkeypatch.setattr(ssettings.time, "monotonic", lambda: now[0])

    state.handle_input(ctx, {"btn_up": True})
    assert state._wifi_password[0] == "A"

    now[0] = 10.499
    state.handle_input(ctx, {})
    assert state._wifi_password[0] == "A"

    now[0] = 10.5
    state.handle_input(ctx, {})
    assert state._wifi_password[0] == "B"

    now[0] = 10.599
    state.handle_input(ctx, {})
    assert state._wifi_password[0] == "B"

    now[0] = 10.6
    state.handle_input(ctx, {})
    assert state._wifi_password[0] == "C"


def test_wifi_password_down_hold_repeats_and_stops_on_release(monkeypatch):
    ctx = _Ctx()
    ctx.current_button_state = {"btn_down": True}
    state = SettingsState()
    state._enter_wifi_password(
        {"ssid": "Home", "rssi": 80, "security": "WPA2", "secured": True}
    )
    now = [20.0]
    monkeypatch.setattr(ssettings.time, "monotonic", lambda: now[0])

    state.handle_input(ctx, {"btn_down": True})
    initial = state._wifi_password[0]

    now[0] = 20.7
    state.handle_input(ctx, {})
    expected_index = (
        ssettings.WIFI_PASSWORD_CHARACTERS.index(initial) - 3
    ) % len(ssettings.WIFI_PASSWORD_CHARACTERS)
    assert state._wifi_password[0] == ssettings.WIFI_PASSWORD_CHARACTERS[expected_index]

    ctx.current_button_state = {"btn_down": False}
    released = state._wifi_password[0]
    now[0] = 21.5
    state.handle_input(ctx, {})
    assert state._wifi_password[0] == released
    assert state._wifi_character_repeat_direction == 0
    assert state._wifi_character_repeat_next_at is None
