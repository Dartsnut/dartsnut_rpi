from states import menu as smenu
from states import settings as ssettings


def test_settings_rssi_and_level_mappings():
    assert ssettings._rssi_to_color(None) == (128, 128, 128)
    assert ssettings._rssi_to_color(-60) == (0, 255, 0)
    assert ssettings._rssi_to_color(-80) == (255, 0, 0)

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


def test_menu_firmware_flag_helpers(monkeypatch):
    removed = []
    monkeypatch.setattr(smenu.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(smenu.os, "remove", lambda p: removed.append(p))
    assert smenu._check_firmware_updated_flag() is True
    smenu._remove_firmware_updated_flag()
    assert removed and removed[0] == "/tmp/firmware_updated.flag"
