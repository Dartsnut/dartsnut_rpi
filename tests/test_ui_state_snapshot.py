import json

from runtime import display_loop


def test_write_ui_state_snapshot_writes_runtime_state(tmp_path, monkeypatch):
    snapshot = tmp_path / "state.json"
    monkeypatch.setattr(display_loop, "_UI_STATE_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.setattr(display_loop, "_ui_snapshot_last_write_at", 0.0)
    monkeypatch.setattr(display_loop, "_ui_snapshot_last_payload", "")

    ctx = type("Ctx", (), {})()
    ctx.state_str = "widget"
    ctx.game_id = "queued"
    ctx.game = {"game_id": "running"}
    ctx.page_index = 0
    ctx.pages = [{"uuid": "p1", "title": "Home", "widgets": [{"id": "w1"}]}]
    ctx.wifi_connected = True
    ctx.internet_connected = False

    display_loop.write_ui_state_snapshot(ctx)

    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert data["state"] == "widget"
    assert data["game_id"] == "queued"
    assert data["running_game_id"] == "running"
    assert data["page_uuid"] == "p1"
    assert data["widget_ids"] == ["w1"]
    assert data["wifi_connected"] is True
