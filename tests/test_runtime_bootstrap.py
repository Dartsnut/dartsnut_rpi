import json

from runtime import bootstrap


def test_ensure_device_info_id_noop_when_id_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(
        json.dumps({"id": "AA:BB:CC:DD:EE:FF"}),
        encoding="utf-8",
    )

    device_info = {"id": "AA:BB:CC:DD:EE:FF"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"


def test_ensure_device_info_id_backfills_from_ble_mac_and_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({"ble_mac": "aa:bb:cc:dd:ee:ff"}), encoding="utf-8")

    device_info = {"ble_mac": "aa:bb:cc:dd:ee:ff"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert "id" not in persisted


def test_ensure_device_info_id_persists_when_incoming_identity_complete(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({}), encoding="utf-8")

    device_info = {
        "ble_mac": "aa:bb:cc:dd:ee:ff",
        "serial": "SN-001",
        "model": "PixelDart",
    }
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"
    assert persisted["serial"] == "SN-001"
    assert persisted["model"] == "PixelDart"


def test_ensure_device_info_id_reads_missing_identity_from_boot(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({}), encoding="utf-8")

    monkeypatch.setattr(
        bootstrap,
        "_load_boot_device_identity",
        lambda: {"serial": "BOOT-SN", "model": "PixelBoard"},
    )

    device_info = {"ble_mac": "aa:bb:cc:dd:ee:ff"}
    out = bootstrap._ensure_device_info_id(device_info)

    assert out["id"] == "AA:BB:CC:DD:EE:FF"
    assert out["serial"] == "BOOT-SN"
    assert out["model"] == "PixelBoard"
    persisted = json.loads(device_path.read_text(encoding="utf-8"))
    assert persisted["id"] == "AA:BB:CC:DD:EE:FF"
    assert persisted["serial"] == "BOOT-SN"
    assert persisted["model"] == "PixelBoard"
