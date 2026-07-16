"""Guard against duplicate concurrent widget background downloads."""

from __future__ import annotations

import threading

import widget_lifecycle as wl
from core.app_metadata import write_app_metadata


def test_download_widget_async_second_call_skipped_while_inflight(monkeypatch):
    threads_started = []
    real_thread = threading.Thread

    def counting_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        threads_started.append(t)
        return t

    monkeypatch.setattr(threading, "Thread", counting_thread)

    release = threading.Event()

    def blocking_download(widget_id, url, md5, download_info=None):
        release.wait(timeout=5.0)
        return False

    monkeypatch.setattr(wl, "download_app", blocking_download)
    wl._widget_background_download_inflight.discard("dupwid")

    wl.download_widget_async("dupwid", "http://example/a.tar.gz", "abc", None)
    wl.download_widget_async("dupwid", "http://example/a.tar.gz", "abc", None)

    assert len(threads_started) == 1
    release.set()
    threads_started[0].join(timeout=5.0)


def test_check_and_update_widget_version_sends_token_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = {"headers": None, "params": None}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"version": "1.0.0"}}

    def _get(_url, **kwargs):
        calls["headers"] = kwargs.get("headers")
        calls["params"] = kwargs.get("params")
        return _Resp()

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})
    monkeypatch.setattr(wl.requests, "get", _get)

    assert wl.check_and_update_widget_version("clock") == (True, {"version": "1.0.0"})
    assert calls["headers"] == {"Token": "abc"}
    assert calls["params"] == {"id": "clock", "version": ""}


def test_check_and_update_widget_version_uses_backend_metadata_not_conf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "clock"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text(
        '{"id":"packaged-clock","type":"widget","version":"0.1.0"}',
        encoding="utf-8",
    )
    write_app_metadata("clock", {"id": "clock", "type": "widget", "version": "2.0.0"})

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"widget_id": "clock", "version": "2.0.0"}}

    calls = {"params": None}

    def _get(*_args, **kwargs):
        calls["params"] = kwargs.get("params")
        return _Resp()

    monkeypatch.setattr(wl.requests, "get", _get)

    assert wl.check_and_update_widget_version("clock") == (False, {"widget_id": "clock", "version": "2.0.0"})
    assert calls["params"] == {"id": "clock", "version": "2.0.0"}
