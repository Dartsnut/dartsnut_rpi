import subprocess

import widget_lifecycle as wl
from core.app_metadata import read_app_metadata


def test_download_app_installs_into_widget_id_and_writes_backend_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    (tmp_path / "downloads").mkdir()

    installed = []
    ensured = []
    download_path = tmp_path / "downloads" / "mygame.tar.gz"
    download_path.write_bytes(b"payload")

    monkeypatch.setattr(
        wl,
        "install_app_tarball",
        lambda tar, app_id: installed.append((tar, app_id)) or str(tmp_path / "apps" / app_id),
    )
    monkeypatch.setattr(
        wl,
        "ensure_app_venv_after_extract",
        lambda tar, url=None, app_id=None: ensured.append((tar, url, app_id)) or True,
    )

    def _run(cmd, **kwargs):
        if cmd[0] == "wget":
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[0] == "md5sum":
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef  file\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(wl.subprocess, "run", _run)

    url = "https://example.com/mygame.tar.gz"
    info = {"version": "2.0.0", "widget_download_url": url, "widget_download_md5": "deadbeef"}
    assert wl.download_app("clock", url, "deadbeef", download_info=info) is True
    assert installed == [("downloads/mygame.tar.gz", "clock")]
    assert ensured == [("downloads/mygame.tar.gz", url, "clock")]
    assert read_app_metadata("clock")["version"] == "2.0.0"


def test_download_app_retries_on_transient_wget_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    (tmp_path / "downloads").mkdir()

    import core.retry as retry

    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        wl, "ensure_app_venv_after_extract", lambda tar, url=None, app_id=None: True
    )
    monkeypatch.setattr(wl, "install_app_tarball", lambda tar, app_id: str(tmp_path / "apps" / app_id))

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
    info = {"version": "2.0.0", "widget_download_url": url, "widget_download_md5": "deadbeef"}
    assert wl.download_app("clock", url, "deadbeef", download_info=info) is True
    assert attempts["wget"] == 3


def test_download_app_rejects_non_targz_without_retry(monkeypatch):
    calls = {"n": 0}

    def _never_run(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(wl.subprocess, "run", _never_run)
    assert wl.download_app("clock", "https://example.com/file.zip", "abc") is False
    assert calls["n"] == 0
