import json
import os

from machine_state_service import MachineStateService


def _service_for_tests():
    return MachineStateService(
        ctx=None,
        set_brightness_hardware=lambda _v: None,
        get_device_info=lambda: {},
        reload_pages_from_conf=lambda _ctx: None,
    )


def test_reset_device_to_factory_fields_keeps_only_required_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("device.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "name": "Custom Name",
                "serial": "SN001",
                "model": "PixelBoard",
                "id": "AA:BB:CC:DD:EE:FF",
                "brightness": 42,
                "volume": 7,
                "ip_address": "1.2.3.4",
                "ssid": "my-wifi",
                "dim_window_enabled": True,
                "dim_window_start": "21:00",
                "dim_window_end": "07:00",
                "dim_level": 3,
                "dim_restore_seconds": 9,
            },
            f,
        )

    service = _service_for_tests()
    service.reset_device_to_factory_fields()

    with open("device.json", "r", encoding="utf-8") as f:
        payload = json.load(f)

    assert payload["name"] == "Custom Name"
    assert payload["serial"] == "SN001"
    assert payload["model"] == "PixelBoard"
    assert payload["id"] == "AA:BB:CC:DD:EE:FF"
    assert payload["brightness"] == 9
    assert payload["brightness_format"] == "level_0_9"
    assert payload["volume"] == 100
    assert payload["ssid"] == ""
    assert payload["ip_address"] == ""
    assert payload["dim_window_enabled"] is False
    assert payload["dim_window_start"] == "22:00"
    assert payload["dim_window_end"] == "8:00"
    assert payload["dim_level"] == 10
    assert payload["dim_restore_seconds"] == 5
    assert isinstance(payload.get("updated_at"), str)
    assert payload["updated_at"] != ""


def test_reset_device_to_factory_fields_deletes_api_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    with open("device.json", "w", encoding="utf-8") as f:
        json.dump({"brightness": 42, "volume": 7}, f)

    from runtime.api_token_store import get_api_token, preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})

    service = _service_for_tests()
    service.reset_device_to_factory_fields()

    assert get_api_token() == ""


def test_clear_apps_directory_contents_removes_all_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps/game_a", exist_ok=True)
    os.makedirs("apps/widget_b", exist_ok=True)
    with open("apps/conf.json", "w", encoding="utf-8") as f:
        f.write("{}")
    with open("apps/random.txt", "w", encoding="utf-8") as f:
        f.write("hello")

    service = _service_for_tests()
    service.clear_apps_directory_contents()

    assert os.path.isdir("apps")
    assert os.listdir("apps") == ["conf.json"]
    with open("apps/conf.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["user"] == ""
    assert payload["date"] == ""
    assert payload["pages"] == []
    assert isinstance(payload.get("pages_updated_at"), str)
    assert payload["pages_updated_at"] != ""


def test_set_pages_deferred_reload_writes_conf_without_invoking_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)
    with open("apps/conf.json", "w", encoding="utf-8") as f:
        json.dump({"user": "alice", "date": "2026-03-25", "pages": [{"uuid": "old"}]}, f)

    calls = {"reload": 0}

    service = MachineStateService(
        ctx=object(),
        set_brightness_hardware=lambda _v: None,
        get_device_info=lambda: {},
        reload_pages_from_conf=lambda _ctx: calls.__setitem__("reload", calls["reload"] + 1),
    )

    pages = [{"uuid": "new-page", "widgets": None}]
    service.set_pages(pages, reload_pages=False)

    assert calls["reload"] == 0
    with open("apps/conf.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["user"] == "alice"
    assert payload["date"] == "2026-03-25"
    assert payload["pages"] == [{"uuid": "new-page", "widgets": []}]
    assert isinstance(payload.get("pages_updated_at"), str)
    assert payload["pages_updated_at"] != ""


def test_set_pages_default_behavior_still_reload_immediately(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("apps", exist_ok=True)

    calls = {"reload": 0}
    service = MachineStateService(
        ctx=object(),
        set_brightness_hardware=lambda _v: None,
        get_device_info=lambda: {},
        reload_pages_from_conf=lambda _ctx: calls.__setitem__("reload", calls["reload"] + 1),
    )

    service.set_pages([{"uuid": "page-1", "widgets": []}])
    assert calls["reload"] == 1


def test_set_brightness_preserves_existing_identity_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("device.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "serial": "SN-ORIGINAL",
                "model": "PixelBoard",
                "brightness": "25",
                "volume": "30",
            },
            f,
        )

    service = MachineStateService(
        ctx=None,
        set_brightness_hardware=lambda _v: None,
        get_device_info=lambda: {
            "serial": "SN-HACKED",
            "model": "ChangedModel",
            "brightness": "33",
        },
        reload_pages_from_conf=lambda _ctx: None,
    )

    service.set_brightness(8)

    with open("device.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["serial"] == "SN-ORIGINAL"
    assert payload["model"] == "PixelBoard"
    assert payload["brightness"] == "8"
    assert payload["brightness_format"] == "level_0_9"


def test_set_brightness_uses_custom_calibration_curve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("device.json", "w", encoding="utf-8") as f:
        json.dump({"brightness": "4", "hardware_version": "444f"}, f)
    with open("brightness_calibration.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "format_version": 1,
                "hardware_version": "444f",
                "values": [10, 15, 21, 27, 34, 45, 59, 69, 84, 95],
                "current_level": 4,
            },
            f,
        )
    hardware = []
    service = MachineStateService(
        ctx=None,
        set_brightness_hardware=hardware.append,
        get_device_info=lambda: {
            "brightness": "4",
            "hardware_version": "444f",
        },
        reload_pages_from_conf=lambda _ctx: None,
    )

    service.set_brightness(8)

    assert hardware == [84]
    with open("device.json", "r", encoding="utf-8") as f:
        assert json.load(f)["brightness"] == "8"


def test_set_volume_does_not_introduce_identity_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open("device.json", "w", encoding="utf-8") as f:
        json.dump({"brightness": "20", "volume": "10"}, f)

    service = MachineStateService(
        ctx=None,
        set_brightness_hardware=lambda _v: None,
        get_device_info=lambda: {
            "serial": "SN-NEW",
            "model": "PixelDart",
            "volume": "88",
        },
        reload_pages_from_conf=lambda _ctx: None,
    )

    service.set_volume(55)

    with open("device.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert "serial" not in payload
    assert "model" not in payload
    assert payload["volume"] == "55"
