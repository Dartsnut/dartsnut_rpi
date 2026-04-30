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
