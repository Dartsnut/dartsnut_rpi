import hashlib
import io
import subprocess
import tarfile

import game_lifecycle
from game_lifecycle import (
    compare_game_versions,
    ensure_game_downloaded,
    local_game_version_matches,
    resolve_game_version_for_sync,
)


def test_ensure_game_downloaded_returns_true_when_game_already_exists(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/chess"))
    called = {"requests": 0, "download": 0}

    def _never_requests(*args, **kwargs):
        called["requests"] += 1
        raise AssertionError("requests.get should not be called")

    def _never_download(*args, **kwargs):
        called["download"] += 1
        raise AssertionError("_download_game_file should not be called")

    monkeypatch.setattr("game_lifecycle.requests.get", _never_requests)
    monkeypatch.setattr("game_lifecycle._download_game_file", _never_download)

    assert ensure_game_downloaded("chess") is True
    assert called["requests"] == 0
    assert called["download"] == 0


def test_ensure_game_downloaded_syncs_pico8_favourites_when_already_exists(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/pico8"))
    monkeypatch.setattr("game_lifecycle.get_pico8_key", lambda: "secret-key")
    calls = []
    monkeypatch.setattr(
        "game_lifecycle.sync_pico8_favourites",
        lambda key: calls.append(key),
    )

    assert ensure_game_downloaded("pico8") is True
    assert calls == ["secret-key"]


def test_ensure_game_downloaded_skips_pico8_sync_when_key_missing(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/pico8"))
    monkeypatch.setattr("game_lifecycle.get_pico8_key", lambda: "")
    calls = []
    monkeypatch.setattr(
        "game_lifecycle.sync_pico8_favourites",
        lambda key: calls.append(key),
    )

    assert ensure_game_downloaded("pico8") is True
    assert calls == []


def test_ensure_game_downloaded_downloads_when_missing(monkeypatch):
    calls = {"download": []}
    state = {"exists": False}

    def _isdir(path):
        return state["exists"] and path.endswith("/apps/chess")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_download_url": "https://example.com/chess.zip",
                    "game_download_md5": "abc123",
                }
            }

    def _download_game_file(url, md5, game_id):
        calls["download"].append((url, md5, game_id))
        state["exists"] = True
        return True

    monkeypatch.setattr("game_lifecycle.os.path.isdir", _isdir)
    monkeypatch.setattr("game_lifecycle.requests.get", lambda url, **kwargs: _Resp())
    monkeypatch.setattr("game_lifecycle._download_game_file", _download_game_file)

    assert ensure_game_downloaded("chess") is True
    assert calls["download"] == [("https://example.com/chess.zip", "abc123", "chess")]


def test_ensure_game_downloaded_syncs_pico8_after_download(monkeypatch):
    calls = {"download": [], "sync": []}
    state = {"exists": False}

    def _isdir(path):
        return state["exists"] and path.endswith("/apps/pico8")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_download_url": "https://example.com/pico8.tar.gz",
                    "game_download_md5": "abc123",
                }
            }

    def _download_game_file(url, md5, game_id):
        calls["download"].append((url, md5, game_id))
        state["exists"] = True
        return True

    monkeypatch.setattr("game_lifecycle.os.path.isdir", _isdir)
    monkeypatch.setattr("game_lifecycle.requests.get", lambda url, **kwargs: _Resp())
    monkeypatch.setattr("game_lifecycle._download_game_file", _download_game_file)
    monkeypatch.setattr("game_lifecycle.get_pico8_key", lambda: "secret-key")
    monkeypatch.setattr(
        "game_lifecycle.sync_pico8_favourites",
        lambda key: calls["sync"].append(key),
    )

    assert ensure_game_downloaded("pico8") is True
    assert calls["download"] == [
        ("https://example.com/pico8.tar.gz", "abc123", "pico8")
    ]
    assert calls["sync"] == ["secret-key"]


def test_ensure_game_downloaded_does_not_sync_non_pico8(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/chess"))
    calls = []
    monkeypatch.setattr("game_lifecycle.get_pico8_key", lambda: "secret-key")
    monkeypatch.setattr(
        "game_lifecycle.sync_pico8_favourites",
        lambda key: calls.append(key),
    )

    assert ensure_game_downloaded("chess") is True
    assert calls == []


def test_ensure_game_downloaded_sends_token_header(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = {"headers": None}
    state = {"exists": False}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_download_url": "https://example.com/chess.zip",
                    "game_download_md5": "abc123",
                }
            }

    def _get(_url, **kwargs):
        calls["headers"] = kwargs.get("headers")
        return _Resp()

    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda _p: state["exists"])
    monkeypatch.setattr("game_lifecycle.requests.get", _get)
    monkeypatch.setattr(
        "game_lifecycle._download_game_file",
        lambda *_a: state.__setitem__("exists", True) or True,
    )

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})

    assert ensure_game_downloaded("chess") is True
    assert calls["headers"] == {"Token": "abc"}


def test_ensure_game_downloaded_redownloads_when_version_mismatch(monkeypatch):
    calls = {"download": []}
    state = {"version": "1.0.0"}

    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda _p: True)
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: state["version"])

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_download_url": "https://example.com/chess.zip",
                    "game_download_md5": "abc123",
                }
            }

    def _download_game_file(url, md5, game_id):
        calls["download"].append((url, md5, game_id))
        state["version"] = "2.0.0"
        return True

    monkeypatch.setattr("game_lifecycle.requests.get", lambda url, **kwargs: _Resp())
    monkeypatch.setattr("game_lifecycle._download_game_file", _download_game_file)

    assert ensure_game_downloaded("chess", "2.0.0") is True
    assert calls["download"] == [("https://example.com/chess.zip", "abc123", "chess")]


def test_ensure_game_downloaded_retries_info_fetch(monkeypatch):
    calls = {"download": [], "requests": 0}
    state = {"exists": False}

    def _isdir(path):
        return state["exists"] and path.endswith("/apps/chess")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": {
                    "game_download_url": "https://example.com/chess.tar.gz",
                    "game_download_md5": "abc123",
                }
            }

    def _flaky_get(url, **kwargs):
        calls["requests"] += 1
        if calls["requests"] < 2:
            raise Exception("connection reset")
        return _Resp()

    def _download_game_file(url, md5, game_id):
        calls["download"].append((url, md5, game_id))
        state["exists"] = True
        return True

    import core.retry as retry

    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr("game_lifecycle.os.path.isdir", _isdir)
    monkeypatch.setattr("game_lifecycle.requests.get", _flaky_get)
    monkeypatch.setattr("game_lifecycle._download_game_file", _download_game_file)

    assert ensure_game_downloaded("chess") is True
    assert calls["requests"] == 2
    assert calls["download"] == [("https://example.com/chess.tar.gz", "abc123", "chess")]


def test_download_game_file_installs_archive_into_game_id_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        for name, content in {
            "not-chess/conf.json": '{"id": "not-chess", "type": "game"}',
            "not-chess/main.py": "print('ok')\n",
        }.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    payload = tar_buffer.getvalue()
    checksum = hashlib.md5(payload).hexdigest()

    def _run(cmd, **kwargs):
        if cmd[0] == "wget":
            (tmp_path / "downloads" / "random-name.tar.gz").write_bytes(payload)
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[0] == "md5sum":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"{checksum}  downloads/random-name.tar.gz\n"
            )
        if cmd[0] == "tar":
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(game_lifecycle.subprocess, "run", _run)
    monkeypatch.setattr(
        game_lifecycle, "ensure_app_venv", lambda game_id: game_id == "chess"
    )

    assert game_lifecycle._download_game_file(
        "https://example.com/random-name.tar.gz", checksum, "chess"
    ) is True
    assert (tmp_path / "apps" / "chess" / "conf.json").is_file()
    assert not (tmp_path / "apps" / "not-chess").exists()


def test_local_game_version_matches_true_when_versions_equal(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/chess"))
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: "1.2.3")

    assert local_game_version_matches("chess", "1.2.3") is True


def test_local_game_version_matches_false_when_remote_version_empty(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/chess"))
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: "1.2.3")

    assert local_game_version_matches("chess", "") is False


def test_local_game_version_matches_false_when_versions_differ(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/chess"))
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: "1.2.3")

    assert local_game_version_matches("chess", "2.0.0") is False


def test_local_game_version_matches_true_when_local_is_newer(monkeypatch):
    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda p: p.endswith("/apps/chess"))
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: "2.1.0")

    assert local_game_version_matches("chess", "2.0.0") is True


def test_ensure_game_downloaded_skips_download_when_local_is_newer(monkeypatch):
    called = {"requests": 0, "download": 0}

    monkeypatch.setattr("game_lifecycle.os.path.isdir", lambda _p: True)
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: "3.0.0")

    def _never_requests(*args, **kwargs):
        called["requests"] += 1
        raise AssertionError("requests.get should not be called")

    def _never_download(*args, **kwargs):
        called["download"] += 1
        raise AssertionError("_download_game_file should not be called")

    monkeypatch.setattr("game_lifecycle.requests.get", _never_requests)
    monkeypatch.setattr("game_lifecycle._download_game_file", _never_download)

    assert ensure_game_downloaded("chess", "2.0.0") is True
    assert called["requests"] == 0
    assert called["download"] == 0


def test_compare_game_versions_orders_dotted_numeric_parts():
    assert compare_game_versions("1.2.3", "1.2.3") == 0
    assert compare_game_versions("2.0.0", "1.9.9") == 1
    assert compare_game_versions("1.0", "1.0.0") == 0


def test_resolve_game_version_for_sync_prefers_higher_version(monkeypatch):
    monkeypatch.setattr("game_lifecycle.get_local_game_version", lambda _gid: "2.5.0")

    assert resolve_game_version_for_sync("chess", "2.0.0") == "2.5.0"
    assert resolve_game_version_for_sync("chess", "3.0.0") == "3.0.0"
