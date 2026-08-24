from __future__ import annotations

import widget_lifecycle as wl
from core.app_metadata import write_app_metadata


class _Shm:
    def __init__(self, size: int):
        self.buf = bytearray(size)


class _Process:
    pid = 1234

    def poll(self):
        return None


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
    assert result["widgets"][0]["loading"] is False
    assert requested == [("digitalclock", True)]


def test_start_page_process_marks_spawned_widget_loading_until_first_frame(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "digitalclock").mkdir(parents=True)
    write_app_metadata("digitalclock", {"id": "digitalclock", "type": "widget"})
    created = {}

    def _shared_memory(name, create=False, size=None):
        if not create:
            raise FileNotFoundError
        shm = _Shm(size)
        created["shm"] = shm
        return shm

    monkeypatch.setattr(wl.shared_memory, "SharedMemory", _shared_memory)
    monkeypatch.setattr("core.app_env.app_venv_ready", lambda _widget_id: True)
    monkeypatch.setattr(wl, "ensure_app_venv", lambda _widget_id: True)
    monkeypatch.setattr(wl, "process_widget_fields", lambda _widget_id, fields: fields)
    monkeypatch.setattr(wl, "app_python_command", lambda *_args: ["python", "main.py"])
    monkeypatch.setattr(wl, "get_user_data_store_path", lambda _widget_id: "/tmp/data.json")
    monkeypatch.setattr(wl.subprocess, "Popen", lambda *_args, **_kwargs: _Process())
    monkeypatch.setattr(wl, "signal_process_group", lambda *_args: None)

    result = wl.start_page_process(
        {
            "uuid": "page-1",
            "duration": "15",
            "enabled": True,
            "widgets": [
                {
                    "id": "digitalclock",
                    "position": [0, 0, 127, 31],
                    "fields": {},
                }
            ],
        }
    )

    widget = result["widgets"][0]
    assert widget["loading"] is True
    assert widget["process"].pid == 1234
    assert created["shm"].buf[0] == 1


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
        "loading": False,
        "has_small_widget": None,
    }
    page = {"uuid": "page-2"}

    wl.restart_widget_process(widget_entry, page, 0)

    assert requested == [("sunrisesunset", False)]
    assert widget_entry["loading"] is False


def test_restart_widget_process_starts_fresh_loading_cycle(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "digitalclock").mkdir(parents=True)
    write_app_metadata("digitalclock", {"id": "digitalclock", "type": "widget"})
    created = {}

    def _shared_memory(name, create=False, size=None):
        if not create:
            raise FileNotFoundError
        shm = _Shm(size)
        created["shm"] = shm
        return shm

    monkeypatch.setattr(wl.shared_memory, "SharedMemory", _shared_memory)
    monkeypatch.setattr("core.app_env.app_venv_ready", lambda _widget_id: True)
    monkeypatch.setattr(wl, "ensure_app_venv", lambda _widget_id: True)
    monkeypatch.setattr(wl, "process_widget_fields", lambda _widget_id, fields: fields)
    monkeypatch.setattr(wl, "app_python_command", lambda *_args: ["python", "main.py"])
    monkeypatch.setattr(wl, "get_user_data_store_path", lambda _widget_id: "/tmp/data.json")
    monkeypatch.setattr(wl.subprocess, "Popen", lambda *_args, **_kwargs: _Process())

    widget_entry = {
        "widget": {
            "id": "digitalclock",
            "position": [0, 0, 127, 31],
            "fields": {},
        },
        "process": None,
        "shm": None,
        "loading": False,
        "has_small_widget": False,
    }

    wl.restart_widget_process(widget_entry, {"uuid": "page-1"}, 0)

    assert widget_entry["loading"] is True
    assert widget_entry["has_small_widget"] is None
    assert widget_entry["process"].pid == 1234
    assert created["shm"].buf[0] == 1
