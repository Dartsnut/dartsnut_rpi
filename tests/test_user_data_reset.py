import json

import python_websocket.user_data_operations as udo


def test_reset_user_data_file_writes_defaults(tmp_path, monkeypatch):
    data_file = tmp_path / "user_data.json"
    temp_file = tmp_path / "game_start.json"
    monkeypatch.setattr(udo, "PERSISTENT_DATA_FILE", str(data_file))
    monkeypatch.setattr(udo, "TEMP_GAME_START_FILE", str(temp_file))
    monkeypatch.setattr(udo, "PERSISTENT_DATA_DIR", str(tmp_path))

    data_file.write_text(
        json.dumps(
            {
                "user_id": "u1",
                "jwt_token": "t",
                "refresh_token": "r",
                "game_playtimes": {"g1": 99},
            }
        ),
        encoding="utf-8",
    )
    temp_file.write_text('{"game_id":"g1","start_time":0}', encoding="utf-8")

    udo.reset_user_data_file()

    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["user_id"] == ""
    assert data["jwt_token"] == ""
    assert data["refresh_token"] == ""
    assert data["game_playtimes"] == {}
    assert not temp_file.exists()
