from pathlib import Path

import update_repair


def test_update_repair_marker_lifecycle(tmp_path, monkeypatch):
    markers = (
        tmp_path / "boot" / "firmware" / "dartsnut_update_pending",
        tmp_path / "boot" / "dartsnut_update_pending",
        tmp_path / "var" / "lib" / "dartsnut" / "update_pending",
    )
    monkeypatch.setattr(update_repair, "PENDING_UPDATE_MARKERS", tuple(map(str, markers)))
    markers[0].parent.mkdir(parents=True, exist_ok=True)
    markers[1].parent.mkdir(parents=True, exist_ok=True)

    written = update_repair.mark_update_repair_pending()

    assert written == str(markers[0])
    assert markers[0].read_text(encoding="utf-8") == "pending\n"

    for marker in markers:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("pending\n", encoding="utf-8")

    update_repair.clear_update_repair_pending()

    assert not any(marker.exists() for marker in markers)
