import base64
import hashlib
import io
import json
import tarfile

from python_websocket import file_operations as fops
from core.app_metadata import write_app_metadata
from core.app_metadata import read_app_metadata


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
    calls = {"headers": None, "params": None}

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
            calls["params"] = kwargs.get("params")
            return _Resp()
        raise AssertionError("download URL should be stubbed before requests.get")

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})
    write_app_metadata("g1", {"id": "g1", "type": "game", "version": "1.2.3"})
    monkeypatch.setattr(fops.requests, "get", _get)

    fops.DOWNLOAD_PROGRESS.clear()
    fops._DOWNLOAD_CANCEL_REQUESTED.discard("g1")
    fops._DOWNLOAD_KEYS_IN_FLIGHT.clear()
    fops._download_game_worker("g1")

    assert calls["headers"] == {"Token": "abc"}
    assert calls["params"] == {"id": "g1", "version": "1.2.3"}


def test_download_app_requires_game_id():
    result = fops.download_app("https://example.com/game.tar.gz", "abc123")

    assert result["error_code"] == "3002"


def test_download_app_rejects_url_not_matching_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class _InfoResp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_id": "chess",
                    "version": "2.0.0",
                    "game_download_url": "https://example.com/backend.tar.gz",
                    "game_download_md5": "backend-md5",
                }
            }

    monkeypatch.setattr(fops.requests, "get", lambda *_a, **_k: _InfoResp())
    monkeypatch.setattr(fops.os, "system", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("wget should not run")))
    fops._DOWNLOAD_KEYS_IN_FLIGHT.clear()

    result = fops.download_app("https://example.com/caller.tar.gz", "caller-md5", game_id="chess")

    assert result["error_code"] == "3001"


def test_download_game_worker_with_url_installs_archive_into_game_id_folder(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        for name, content in {
            "not-chess/conf.json": json.dumps(
                {"id": "not-chess", "type": "game", "version": "1.2.3", "preview": ["packaged"]}
            ),
            "not-chess/main.py": "print('ok')\n",
        }.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    payload = tar_buffer.getvalue()
    checksum = hashlib.md5(payload).hexdigest()

    class _DownloadResp:
        status_code = 200
        headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @staticmethod
        def iter_content(chunk_size=8192):
            yield payload

    class _InfoResp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_id": "chess",
                    "game_name": "Backend Chess",
                    "version": "2.0.0",
                    "game_download_url": "https://example.com/not-chess.tar.gz",
                    "game_download_md5": checksum,
                    "main_cover": "covers/chess.png",
                }
            }

    def _get(url, **kwargs):
        if "api.dartsnut.com" in url:
            return _InfoResp()
        return _DownloadResp()

    monkeypatch.setattr(fops.requests, "get", _get)
    monkeypatch.setattr(fops, "ensure_app_venv", lambda game_id: game_id == "chess")
    fops.DOWNLOAD_PROGRESS.clear()
    fops._DOWNLOAD_CANCEL_REQUESTED.clear()
    fops._DOWNLOAD_KEYS_IN_FLIGHT.clear()

    fops._download_game_worker_with_url(
        "chess", "https://example.com/not-chess.tar.gz", checksum
    )

    assert (tmp_path / "apps" / "chess" / "conf.json").is_file()
    assert not (tmp_path / "apps" / "not-chess").exists()
    assert fops.DOWNLOAD_PROGRESS["chess"]["status"] == "completed"
    assert fops.DOWNLOAD_PROGRESS["chess"]["version"] == "2.0.0"
    assert read_app_metadata("chess")["preview_urls"] == ["covers/chess.png"]
