import base64
import json

from python_websocket import file_operations as fops


def test_get_download_progress_single_and_list():
    fops.DOWNLOAD_PROGRESS.clear()
    fops.DOWNLOAD_PROGRESS["g1"] = {"game_id": "g1", "progress": 50, "status": "downloading", "error": None}
    single = fops.get_download_progress("g1")
    listed = fops.get_download_progress(["g1", "g2"])
    assert single["action"] == "get_download_progress"
    assert listed["progresses"]["g2"]["status"] == "not_found"


def test_cancel_game_download_marks_entry_canceled():
    fops.DOWNLOAD_PROGRESS.clear()
    fops._DOWNLOAD_CANCEL_REQUESTED.clear()
    fops.DOWNLOAD_PROGRESS["g1"] = {
        "game_id": "g1",
        "progress": 20,
        "status": "downloading",
        "error": None,
    }

    fops.cancel_game_download("g1")
    progress = fops.get_download_progress("g1")

    assert progress["status"] == "downloading"
    assert "g1" in fops._DOWNLOAD_CANCEL_REQUESTED


def test_create_and_list_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    create = fops.create_directory(None, "/demo")
    listed = fops.get_file_list("/demo")
    duplicate = fops.create_directory(None, "demo")
    assert create["message"] == "Success"
    assert listed["action"] == "list_files"
    assert duplicate["error_code"] == "1006"


def test_receive_and_send_file_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    payload = base64.b64encode(b"hello").decode("utf-8")
    recv = fops.receive_file(None, {"file_name": "a.txt", "file_data": payload})
    send = fops.send_file(None, {"file_name": "a.txt"})
    assert recv["message"] == "Success"
    assert base64.b64decode(send["file_data"]) == b"hello"


def test_get_file_md5_and_remove_directory_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "b.txt").write_text("x", encoding="utf-8")
    md5 = fops.get_file_md5(None, "b.txt")
    missing = fops.remove_directory(None, "missing")
    assert md5["action"] == "get_file_md5"
    assert missing["error_code"] == "1002"


def test_download_game_worker_sends_token_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = {"headers": None}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_download_url": "https://example.com/game.zip",
                    "game_download_md5": "abc123",
                }
            }

    def _get(url, **kwargs):
        if "api.dartsnut.com" in url:
            calls["headers"] = kwargs.get("headers")
            return _Resp()
        raise AssertionError("download URL should be stubbed before requests.get")

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})
    monkeypatch.setattr(fops.requests, "get", _get)

    fops.DOWNLOAD_PROGRESS.clear()
    fops._DOWNLOAD_CANCEL_REQUESTED.discard("g1")
    fops._DOWNLOAD_KEYS_IN_FLIGHT.clear()
    fops._download_game_worker("g1")

    assert calls["headers"] == {"Token": "abc"}
