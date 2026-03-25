from game_lifecycle import ensure_game_downloaded, local_game_version_matches


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
    monkeypatch.setattr("game_lifecycle.requests.get", lambda url: _Resp())
    monkeypatch.setattr("game_lifecycle.download_app", _download_app)

    assert ensure_game_downloaded("chess") is True
    assert calls["download"] == [("https://example.com/chess.zip", "abc123")]


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
