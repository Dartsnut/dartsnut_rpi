import json

from runtime import pixeldarts_hardware as hw


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
