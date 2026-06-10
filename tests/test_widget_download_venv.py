import subprocess

import widget_lifecycle as wl


def test_download_app_calls_ensure_app_venv_after_extract(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    (tmp_path / "downloads").mkdir()

    ensured = []
    download_path = tmp_path / "downloads" / "mygame.tar.gz"
    download_path.write_bytes(b"payload")

    monkeypatch.setattr(
        wl,
        "ensure_app_venv_after_extract",
        lambda tar, url=None, app_id=None: ensured.append((tar, url)) or True,
    )

    def _run(cmd, **kwargs):
        if cmd[0] == "wget":
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[0] == "md5sum":
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef  file\n")
        if cmd[0] == "tar":
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(wl.subprocess, "run", _run)

    url = "https://example.com/mygame.tar.gz"
    assert wl.download_app(url, "deadbeef") is True
    assert ensured and ensured[0][1] == url


def test_download_app_retries_on_transient_wget_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    (tmp_path / "downloads").mkdir()

    import core.retry as retry

    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        wl, "ensure_app_venv_after_extract", lambda tar, url=None, app_id=None: True
    )

    download_path = tmp_path / "downloads" / "mygame.tar.gz"
    attempts = {"wget": 0}

    def _run(cmd, **kwargs):
        if cmd[0] == "wget":
            attempts["wget"] += 1
            if attempts["wget"] < 3:
                raise subprocess.CalledProcessError(1, "wget")
            download_path.write_bytes(b"payload")
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[0] == "md5sum":
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef  file\n")
        if cmd[0] == "tar":
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(wl.subprocess, "run", _run)

    url = "https://example.com/mygame.tar.gz"
    assert wl.download_app(url, "deadbeef") is True
    assert attempts["wget"] == 3


def test_download_app_rejects_non_targz_without_retry(monkeypatch):
    calls = {"n": 0}

    def _never_run(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(wl.subprocess, "run", _never_run)
    assert wl.download_app("https://example.com/file.zip", "abc") is False
    assert calls["n"] == 0
