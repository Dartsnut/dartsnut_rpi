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
