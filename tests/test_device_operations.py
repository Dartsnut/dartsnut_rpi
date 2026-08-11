import json

from python_websocket import device_operations as dops


def test_get_brightness_and_volume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "device.json").write_text(
        json.dumps({"brightness": "4", "volume": "30"}), encoding="utf-8"
    )
    bright = dops.get_brightness()
    volume = dops.get_volume()
    assert bright["brightness"] == 4
    assert volume["volume"] == 30


def test_set_dim_window_validates_and_updates(monkeypatch):
    class _Svc:
        def __init__(self):
            self.last = None

        def set_dim_window(self, cfg):
            self.last = cfg

    svc = _Svc()
    monkeypatch.setattr(dops, "get_machine_state_service", lambda: svc)
    bad = dops.set_dim_window("bad", "10:10")
    good = dops.set_dim_window("09:00", "10:00", dim_level=20, dim_restore_seconds=30)
    assert bad["error_code"] == "3001"
    assert good["message"] == "Success"
    assert svc.last["dim_window_start"] == "09:00"
