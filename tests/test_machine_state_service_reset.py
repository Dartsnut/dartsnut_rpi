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
                "brightness": 42,
                "volume": 7,
                "ip_address": "1.2.3.4",
                "ssid": "my-wifi",
            },
            f,
        )

    service = _service_for_tests()
    service.reset_device_to_factory_fields()

    with open("device.json", "r", encoding="utf-8") as f:
        payload = json.load(f)

    assert payload == {
        "name": "PixelBoard",
        "serial": "SN001",
        "model": "PixelBoard",
        "brightness": 100,
        "volume": 100,
    }


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
        assert json.load(f) == {"user": "", "date": "", "pages": []}
