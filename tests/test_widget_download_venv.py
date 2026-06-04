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
