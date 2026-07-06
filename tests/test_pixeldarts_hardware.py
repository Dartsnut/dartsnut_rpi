import json

import pytest

from runtime import pixeldarts_hardware as hw


@pytest.fixture(autouse=True)
def _clear_device_type_cache():
    hw.clear_device_type_cache()
    yield
    hw.clear_device_type_cache()


def test_is_pixeldart_device_true_when_live_lsusb_lists_pixeldarts(monkeypatch):
    monkeypatch.setattr(
        hw.subprocess,
        "check_output",
        lambda _cmd: b"Bus 001 Device 004: ID 2d80:444f  PIXELDARTS\n",
    )

    assert hw.is_pixeldart_device() is True
    assert hw.is_pixelboard_device() is False


def test_is_pixeldart_device_ignores_cached_hardware_when_live_lsusb_has_no_pixeldarts(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hardware_version.json").write_text(
        json.dumps({"hardware_version": "444f"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        hw.subprocess,
        "check_output",
        lambda _cmd: b"Bus 001 Device 005: ID 2d80:4a4e Dartsnut usb Codec\n",
    )

    assert hw.is_pixeldart_device() is False
    assert hw.is_pixelboard_device() is True


def test_resolve_prefers_lsusb_over_stale_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hardware_version.json").write_text(
        json.dumps({"hardware_version": "444e"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hw, "probe_lsusb_hardware_version", lambda: "444f")

    assert hw.resolve_pixeldarts_hardware_version() == "444f"
    assert json.loads((tmp_path / ".hardware_version.json").read_text()) == {
        "hardware_version": "444f"
    }


def test_resolve_falls_back_to_cache_when_lsusb_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hardware_version.json").write_text(
        json.dumps({"hardware_version": "444e"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hw, "probe_lsusb_hardware_version", lambda: "")

    assert hw.resolve_pixeldarts_hardware_version() == "444e"
