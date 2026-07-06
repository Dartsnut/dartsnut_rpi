"""Guard against duplicate concurrent widget background downloads."""

from __future__ import annotations

import threading

import widget_lifecycle as wl


def test_download_widget_async_second_call_skipped_while_inflight(monkeypatch):
    threads_started = []
    real_thread = threading.Thread

    def counting_thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        threads_started.append(t)
        return t

    monkeypatch.setattr(threading, "Thread", counting_thread)

    release = threading.Event()

    def blocking_download(url, md5):
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
    calls = {"headers": None}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"version": "1.0.0"}}

    def _get(_url, **kwargs):
        calls["headers"] = kwargs.get("headers")
        return _Resp()

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})
    monkeypatch.setattr(wl.requests, "get", _get)

    assert wl.check_and_update_widget_version("clock") == (True, {"version": "1.0.0"})
    assert calls["headers"] == {"Token": "abc"}
