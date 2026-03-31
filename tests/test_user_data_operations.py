from python_websocket import user_data_operations as uops


def _patch_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(uops, "PERSISTENT_DATA_DIR", str(tmp_path / "var"))
    monkeypatch.setattr(uops, "PERSISTENT_DATA_FILE", str(tmp_path / "var" / "user_data.json"))
    monkeypatch.setattr(uops, "TEMP_GAME_START_FILE", str(tmp_path / "tmp_game.json"))


def test_update_and_get_user_data(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    update = uops.update_user_info(user_id="u1", jwt_token="j", refresh_token="r")
    data = uops.get_user_data()
    assert update["message"] == "Success"
    assert data["user_id"] == "u1"


def test_get_game_playtime_and_add_playtime(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    uops.update_user_info(user_id="u2")
    added = uops.add_game_playtime("g1", 10)
    playtime = uops.get_game_playtime("g1")
    missing = uops.get_game_playtime("")
    assert added["total_playtime_seconds"] == 10
    assert playtime["playtime_seconds"] == 10
    assert missing["error_code"] == "3002"


def test_start_and_stop_game_tracking(tmp_path, monkeypatch):
    _patch_paths(monkeypatch, tmp_path)
    uops.start_game_tracking("g2")
    result = uops.stop_game_tracking()
    assert result["action"] == "stop_game_tracking"
    assert result["duration_seconds"] >= 0
