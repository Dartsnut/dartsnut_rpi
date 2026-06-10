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
        raise AssertionError("download_app should not be called")

    monkeypatch.setattr("game_lifecycle.requests.get", _never_requests)
    monkeypatch.setattr("game_lifecycle.download_app", _never_download)

    assert ensure_game_downloaded("chess") is True
    assert called["requests"] == 0
    assert called["download"] == 0


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

    def _download_app(url, md5):
        calls["download"].append((url, md5))
        state["exists"] = True

    monkeypatch.setattr("game_lifecycle.os.path.isdir", _isdir)
    monkeypatch.setattr("game_lifecycle.requests.get", lambda url, **kwargs: _Resp())
    monkeypatch.setattr("game_lifecycle.download_app", _download_app)

    assert ensure_game_downloaded("chess") is True
    assert calls["download"] == [("https://example.com/chess.zip", "abc123")]


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

    def _download_app(url, md5):
        calls["download"].append((url, md5))
        state["version"] = "2.0.0"

    monkeypatch.setattr("game_lifecycle.requests.get", lambda url, **kwargs: _Resp())
    monkeypatch.setattr("game_lifecycle.download_app", _download_app)

    assert ensure_game_downloaded("chess", "2.0.0") is True
    assert calls["download"] == [("https://example.com/chess.zip", "abc123")]


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

    def _download_app(url, md5):
        calls["download"].append((url, md5))
        state["exists"] = True

    import core.retry as retry

    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr("game_lifecycle.os.path.isdir", _isdir)
    monkeypatch.setattr("game_lifecycle.requests.get", _flaky_get)
    monkeypatch.setattr("game_lifecycle.download_app", _download_app)

    assert ensure_game_downloaded("chess") is True
    assert calls["requests"] == 2
    assert calls["download"] == [("https://example.com/chess.tar.gz", "abc123")]


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
        raise AssertionError("download_app should not be called")

    monkeypatch.setattr("game_lifecycle.requests.get", _never_requests)
    monkeypatch.setattr("game_lifecycle.download_app", _never_download)

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
