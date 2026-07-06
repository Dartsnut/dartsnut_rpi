from types import SimpleNamespace

from PIL import Image, ImageFont

from states import menu as smenu
from states import settings as ssettings
from states import widget as swidget


class _Display:
    def __init__(self):
        self.frame = None

    def update_frame_buffer(self, img):
        self.frame = img


def _icon(color):
    return Image.new("RGBA", (11, 11), color)


def _assets():
    return SimpleNamespace(
        logo_image=Image.new("RGB", (128, 128), (0, 0, 0)),
        game_icon=_icon((10, 10, 10, 255)),
        widget_icon=_icon((20, 20, 20, 255)),
        settings_icon=_icon((30, 30, 30, 255)),
        wifi_disconnect_icon=_icon((0, 0, 255, 255)),
        internet_disconnect_icon=_icon((255, 0, 0, 255)),
        font8=ImageFont.load_default(),
    )


class _Sync:
    def is_bridge_active(self):
        return True

    def is_connected(self):
        return True


def test_settings_rssi_and_level_mappings():
    assert ssettings._rssi_to_color(None) == (128, 128, 128)
    assert ssettings._rssi_to_color(-60) == (0, 255, 0)
    assert ssettings._rssi_to_color(-80) == (255, 0, 0)

    assert ssettings._latency_to_color(None) == (128, 128, 128)
    assert ssettings._latency_to_color(100) == (0, 255, 0)
    assert ssettings._latency_to_color(600) == (0, 255, 0)
    assert ssettings._latency_to_color(601) == (255, 0, 0)

    # Brightness level mapping and inverse
    lvl = ssettings._brightness_raw_to_level("bad")
    assert 1 <= lvl <= 10
    assert ssettings._brightness_level_to_raw(1) == 10
    assert ssettings._brightness_level_to_raw(99) == 95

    # Volume mapping and inverse
    v_lvl = ssettings._volume_raw_to_level("bad")
    assert 0 <= v_lvl <= 10
    assert ssettings._volume_level_to_raw(-1) == 0
    assert ssettings._volume_level_to_raw(99) == 100


def test_brightness_level_mapping_uses_444f_values():
    di_444f = {"hardware_version": "444f"}
    di_444e = {"hardware_version": "444e"}

    assert ssettings._brightness_level_to_raw_for_device(2, di_444f) == 13
    assert ssettings._brightness_level_to_raw_for_device(2, di_444e) == 13
    assert ssettings._brightness_raw_to_level_for_device(42, di_444f) == 6


def test_brightness_display_boxes_keep_nine_slots_with_lowest_level_empty():
    di_default = {"hardware_version": "444e"}
    di_444f = {"hardware_version": "444f"}

    assert ssettings._brightness_raw_to_display_boxes_for_device(10, di_default) == 0
    assert ssettings._brightness_raw_to_display_boxes_for_device(20, di_default) == 3
    assert ssettings._brightness_raw_to_display_boxes_for_device(100, di_default) == 9

    assert ssettings._brightness_raw_to_display_boxes_for_device(10, di_444f) == 0
    assert ssettings._brightness_raw_to_display_boxes_for_device(21, di_444f) == 3
    assert ssettings._brightness_raw_to_display_boxes_for_device(95, di_444f) == 9


def test_bridge_active_wifi_icon_color(monkeypatch):
    ctx = SimpleNamespace(wifi_connected=True)

    class _Sync:
        def is_connected(self):
            return True

    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _Sync())
    monkeypatch.setattr(ssettings, "get_supabase_rest_probe_ok", lambda: False)
    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 50)
    assert ssettings._bridge_active_wifi_icon_color(ctx) == (255, 0, 0)

    monkeypatch.setattr(ssettings, "get_supabase_rest_probe_ok", lambda: True)
    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 50)
    assert ssettings._bridge_active_wifi_icon_color(ctx) == (0, 255, 0)

    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: None)
    assert ssettings._bridge_active_wifi_icon_color(ctx) == (128, 128, 128)

    ctx.wifi_connected = False
    assert ssettings._bridge_active_wifi_icon_color(ctx) == (128, 128, 128)


def test_should_show_bridge_disconnect_icon(monkeypatch):
    ctx = SimpleNamespace(wifi_connected=True)

    class _SyncInactive:
        def is_bridge_active(self):
            return False

    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _SyncInactive())
    assert ssettings.should_show_bridge_disconnect_icon(ctx) is False

    class _Sync:
        def is_bridge_active(self):
            return True

        def is_connected(self):
            return True

    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _Sync())
    monkeypatch.setattr(ssettings, "get_supabase_rest_probe_ok", lambda: True)
    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 50)
    assert ssettings.should_show_bridge_disconnect_icon(ctx) is False

    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 601)
    assert ssettings.should_show_bridge_disconnect_icon(ctx) is True

    monkeypatch.setattr(ssettings, "get_supabase_rest_probe_ok", lambda: False)
    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 50)
    assert ssettings.should_show_bridge_disconnect_icon(ctx) is True


def test_menu_latency_disconnect_icon_uses_600ms_threshold(monkeypatch):
    ctx = SimpleNamespace(
        assets=_assets(),
        display=_Display(),
        menu_select_index=0,
        wifi_connected=True,
        get_device_info=lambda: {},
    )
    monkeypatch.setattr(smenu, "is_pixelboard_device", lambda: False)
    monkeypatch.setattr(smenu.time, "time", lambda: 0.0)
    monkeypatch.setattr(smenu, "_check_firmware_updated_flag", lambda: False)
    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _Sync())
    monkeypatch.setattr(ssettings, "get_supabase_rest_probe_ok", lambda: True)

    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 600)
    smenu.MenuState().update(ctx)
    assert ctx.display.frame.getpixel((117, 0)) != (255, 0, 0)

    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 601)
    smenu.MenuState().update(ctx)
    assert ctx.display.frame.getpixel((117, 0)) == (255, 0, 0)


def test_widget_latency_disconnect_icon_uses_600ms_threshold(monkeypatch):
    ctx = SimpleNamespace(
        assets=_assets(),
        display=_Display(),
        page_freeze=False,
        page_index=0,
        last_page_index=0,
        page_tick=0.0,
        next_page_prepared_index=-1,
        wifi_connected=True,
        pages=[
            {
                "uuid": "page-1",
                "enabled": True,
                "duration": 60,
                "widgets": [],
                "framebuffer": bytearray(128 * 160 * 3),
            }
        ],
    )
    monkeypatch.setattr(swidget.time, "time", lambda: 0.0)
    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _Sync())
    monkeypatch.setattr(ssettings, "get_supabase_rest_probe_ok", lambda: True)

    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 600)
    swidget.WidgetState().update(ctx)
    assert ctx.display.frame.getpixel((117, 0)) != (255, 0, 0)

    monkeypatch.setattr(ssettings, "get_supabase_rest_latency_ms", lambda: 601)
    swidget.WidgetState().update(ctx)
    assert ctx.display.frame.getpixel((117, 0)) == (255, 0, 0)


def test_settings_wifi_icon_color_uses_rssi_when_bridge_inactive(monkeypatch):
    ctx = SimpleNamespace(wifi_connected=True)

    class _Sync:
        def is_bridge_active(self):
            return False

    monkeypatch.setattr(ssettings, "get_remote_sync", lambda: _Sync())
    monkeypatch.setattr(ssettings, "_get_wifi_rssi_cached", lambda: -70)
    assert ssettings._settings_wifi_icon_color(ctx) == (255, 0, 0)


def test_menu_firmware_flag_helpers(monkeypatch):
    removed = []
    monkeypatch.setattr(smenu.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(smenu.os, "remove", lambda p: removed.append(p))
    assert smenu._check_firmware_updated_flag() is True
    smenu._remove_firmware_updated_flag()
    assert removed and removed[0] == "/tmp/firmware_updated.flag"
