import json

from runtime import device_json_identity


def test_verify_repairs_missing_model_from_boot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"brightness": "50"}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {"serial": "S1", "model": "PixelDart"},
    )

    assert device_json_identity.verify_and_repair_device_json(str(path)) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["serial"] == "S1"
    assert data["model"] == "PixelDart"
    assert data["brightness"] == "50"


def test_verify_overwrites_wrong_model_with_boot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "device.json"
    path.write_text(
        json.dumps({"serial": "S1", "model": "Wrong", "volume": "10"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {"serial": "S1", "model": "PixelBoard"},
    )

    assert device_json_identity.verify_and_repair_device_json(str(path)) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "PixelBoard"
    assert data["volume"] == "10"


def test_verify_returns_false_when_no_boot_and_missing_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"serial": "S1"}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    assert device_json_identity.verify_and_repair_device_json(str(path)) is False
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "model" not in data
