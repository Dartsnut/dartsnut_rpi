import json
import logging

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


def test_verify_accepts_serial_only_without_model_error(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"serial": "S1"}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    with caplog.at_level(logging.ERROR):
        assert device_json_identity.verify_and_repair_device_json(str(path)) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "model" not in data
    assert "model" not in caplog.text


def test_apply_factory_serial_assigns_unassigned_when_missing(monkeypatch):
    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    out = device_json_identity.apply_factory_serial_if_needed(
        {"model": "PixelBoard"}, device_id="AA:BB:CC:DD:EE:FF"
    )

    assert out["serial"] == device_json_identity.FACTORY_PLACEHOLDER_SERIAL
    assert out["model"] == "PixelBoard"


def test_apply_factory_serial_noop_when_disk_has_serial(monkeypatch):
    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    out = device_json_identity.apply_factory_serial_if_needed(
        {"serial": "SN-REAL", "model": "PixelDart"},
        device_id="AA:BB:CC:DD:EE:FF",
    )

    assert out["serial"] == "SN-REAL"


def test_apply_factory_serial_noop_when_boot_has_serial(monkeypatch):
    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {"serial": "BOOT-SN", "model": "PixelBoard"},
    )

    out = device_json_identity.apply_factory_serial_if_needed(
        {"model": "PixelBoard"},
        device_id="AA:BB:CC:DD:EE:FF",
    )

    assert "serial" not in out


def test_verify_assigns_factory_serial_when_model_present_no_boot(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"model": "PixelDart"}), encoding="utf-8")

    monkeypatch.setattr(
        device_json_identity,
        "load_boot_device_identity",
        lambda: {},
    )

    assert device_json_identity.verify_and_repair_device_json(str(path)) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["serial"] == device_json_identity.FACTORY_PLACEHOLDER_SERIAL
    assert data["model"] == "PixelDart"
