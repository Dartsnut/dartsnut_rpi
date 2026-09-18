import json

from python_websocket import udp_broadcast


normalize_ip = udp_broadcast.normalize_ip


def test_normalize_ip_invalid_values():
    assert normalize_ip("") is None
    assert normalize_ip("   ") is None
    assert normalize_ip("0.0.0.0") is None
    assert normalize_ip(None) is None  # type: ignore[arg-type]


def test_normalize_ip_valid_values():
    assert normalize_ip("192.168.1.10") == "192.168.1.10"
    assert normalize_ip(" 192.168.1.10 ") == "192.168.1.10"
    assert normalize_ip("10.0.0.5") == "10.0.0.5"


def test_get_device_info_overrides_stale_model_without_rewriting_file(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(json.dumps({"model": "PixelDart"}), encoding="utf-8")
    monkeypatch.delattr(udp_broadcast.get_device_info, "_last_mtime", raising=False)
    monkeypatch.delattr(
        udp_broadcast.get_device_info, "_cached_device_info", raising=False
    )
    monkeypatch.setattr(udp_broadcast, "resolve_device_model", lambda: "PixelBoard")

    device_info = udp_broadcast.get_device_info()

    assert device_info["model"] == "PixelBoard"
    assert json.loads(device_path.read_text(encoding="utf-8"))["model"] == "PixelDart"
