from __future__ import annotations

import widget_lifecycle as wl


def test_start_page_process_keeps_page_and_placeholder_when_widget_missing(monkeypatch):
    requested = []

    def _track_download(widget_id: str, *, force: bool = False):
        requested.append((widget_id, force))

    monkeypatch.setattr(wl, "_request_missing_widget_download", _track_download)
    monkeypatch.setattr(wl.os.path, "isdir", lambda path: False)

    page = {
        "uuid": "page-1",
        "duration": "15",
        "enabled": True,
        "widgets": [
            {
                "id": "digitalclock",
                "position": [0, 0, 127, 127],
                "fields": {},
            }
        ],
    }

    result = wl.start_page_process(page)

    assert result is not None
    assert result["uuid"] == "page-1"
    assert result["enabled"] is True
    assert isinstance(result["framebuffer"], bytearray)
    assert len(result["widgets"]) == 1
    assert result["widgets"][0]["widget"]["id"] == "digitalclock"
    assert result["widgets"][0]["process"] is None
    assert requested == [("digitalclock", True)]


def test_restart_widget_process_requests_download_if_widget_dir_missing(monkeypatch):
    requested = []

    def _track_download(widget_id: str, *, force: bool = False):
        requested.append((widget_id, force))

    monkeypatch.setattr(wl, "_request_missing_widget_download", _track_download)
    monkeypatch.setattr(wl.os.path, "isdir", lambda path: False)

    widget_entry = {
        "widget": {
            "id": "sunrisesunset",
            "position": [0, 128, 63, 159],
            "fields": {},
        },
        "process": None,
        "shm": None,
        "launched": False,
        "has_small_widget": None,
    }
    page = {"uuid": "page-2"}

    wl.restart_widget_process(widget_entry, page, 0)

    assert requested == [("sunrisesunset", False)]
