import base64
import io
import json
import logging
import os
import signal

from PIL import Image

import game_lifecycle as gl


class _Proc:
    def __init__(self, pid=4321, running=True):
        self.pid = pid
        self._running = running

    def poll(self):
        return None if self._running else 1


class _FakeShm:
    def __init__(self, name, size):
        self.name = name
        self.buf = bytearray(size)
        self.closed = False
        self.unlinked = False

    def close(self):
        self.closed = True

    def unlink(self):
        self.unlinked = True


def test_start_game_process_creates_process_and_tracking(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "chess"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    created = {}
    tracked = []
    popen_calls = []

    class _LoadingImg:
        def tobytes(self):
            return b"\x00" * (128 * 160 * 3)

    def _shared_memory(name, create=False, size=None):
        if not create:
            raise FileNotFoundError
        shm = _FakeShm(name, size)
        created["shm"] = shm
        return shm

    monkeypatch.setattr(gl.shared_memory, "SharedMemory", _shared_memory)
    monkeypatch.setattr(gl.assets, "create_loading_image", lambda: _LoadingImg())
    monkeypatch.setattr(gl, "start_game_tracking", lambda gid: tracked.append(gid))
    monkeypatch.setattr(gl, "get_user_data_store_path", lambda gid: f"/tmp/{gid}.json")
    monkeypatch.setattr(gl, "ensure_app_venv", lambda gid, force=False: True)
    monkeypatch.setattr(
        gl.subprocess,
        "Popen",
        lambda cmd, cwd, **kwargs: popen_calls.append((cmd, cwd, kwargs)) or _Proc(),
    )

    game = gl.start_game_process("chess")

    assert game["game_id"] == "chess"
    assert game["launched"] is False
    assert tracked == ["chess"]
    assert popen_calls and popen_calls[0][1] == str(tmp_path / "apps" / "chess")
    assert popen_calls[0][0][0].endswith("apps/chess/.venv/bin/python")
    assert popen_calls[0][0][1] == "main.py"
    assert isinstance(created["shm"].buf, bytearray)


def test_start_game_process_syncs_pico8_before_launch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "pico8"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    events = []

    class _LoadingImg:
        def tobytes(self):
            return b"\x00" * (128 * 160 * 3)

    def _shared_memory(name, create=False, size=None):
        if not create:
            raise FileNotFoundError
        return _FakeShm(name, size)

    monkeypatch.setattr(gl.shared_memory, "SharedMemory", _shared_memory)
    monkeypatch.setattr(gl.assets, "create_loading_image", lambda: _LoadingImg())
    monkeypatch.setattr(gl, "start_game_tracking", lambda gid: events.append(("track", gid)))
    monkeypatch.setattr(gl, "get_user_data_store_path", lambda gid: f"/tmp/{gid}.json")
    monkeypatch.setattr(gl, "ensure_app_venv", lambda gid, force=False: True)
    monkeypatch.setattr(gl, "get_pico8_key", lambda: "secret-key")
    monkeypatch.setattr(
        gl,
        "sync_pico8_favourites",
        lambda key: events.append(("sync", key)),
    )
    monkeypatch.setattr(
        gl.subprocess,
        "Popen",
        lambda cmd, cwd, **kwargs: events.append(("popen", cwd)) or _Proc(),
    )

    game = gl.start_game_process("pico8")

    assert game["game_id"] == "pico8"
    assert events[:2] == [
        ("sync", "secret-key"),
        ("popen", str(tmp_path / "apps" / "pico8")),
    ]


def test_start_game_process_skips_pico8_sync_without_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "pico8"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    events = []

    class _LoadingImg:
        def tobytes(self):
            return b"\x00" * (128 * 160 * 3)

    def _shared_memory(name, create=False, size=None):
        if not create:
            raise FileNotFoundError
        return _FakeShm(name, size)

    monkeypatch.setattr(gl.shared_memory, "SharedMemory", _shared_memory)
    monkeypatch.setattr(gl.assets, "create_loading_image", lambda: _LoadingImg())
    monkeypatch.setattr(gl, "start_game_tracking", lambda gid: events.append(("track", gid)))
    monkeypatch.setattr(gl, "get_user_data_store_path", lambda gid: f"/tmp/{gid}.json")
    monkeypatch.setattr(gl, "ensure_app_venv", lambda gid, force=False: True)
    monkeypatch.setattr(gl, "get_pico8_key", lambda: "")
    monkeypatch.setattr(
        gl,
        "sync_pico8_favourites",
        lambda key: events.append(("sync", key)),
    )
    monkeypatch.setattr(
        gl.subprocess,
        "Popen",
        lambda cmd, cwd, **kwargs: events.append(("popen", cwd)) or _Proc(),
    )

    game = gl.start_game_process("pico8")

    assert game["game_id"] == "pico8"
    assert ("sync", "") not in events
    assert events[0] == ("popen", str(tmp_path / "apps" / "pico8"))


def test_start_game_process_returns_none_when_venv_setup_fails(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "chess"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(gl, "ensure_app_venv", lambda gid, force=False: False)

    assert gl.start_game_process("chess") is None


def test_term_game_process_kills_running_process_and_cleans_resources(monkeypatch):
    kills = []
    stopped = []
    shm = _FakeShm("game_shm", 8)
    g = {"process": _Proc(pid=9876, running=True), "shm": shm, "game_id": "chess"}

    monkeypatch.setattr(gl, "stop_game_tracking", lambda: stopped.append(True))
    monkeypatch.setattr(gl, "terminate_process_group", lambda pid: kills.append((pid, signal.SIGKILL)))

    gl.term_game_process(g)

    assert stopped == [True]
    assert kills and kills[0][0] == 9876
    assert shm.closed is True and shm.unlinked is True
    assert g == {}


def test_load_game_list_decodes_preview_and_get_games_summary(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "chess"
    app_dir.mkdir(parents=True)

    img = Image.new("RGB", (8, 8), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    preview_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "chess", "name": "Chess", "type": "game", "version": "1.2.0", "preview": [preview_b64]}),
        encoding="utf-8",
    )

    game_list = gl.load_game_list()
    summary = gl.get_games_summary()

    assert game_list[0]["id"] == "chess"
    assert game_list[0]["status"] == "ready"
    assert isinstance(game_list[0]["preview"][0], bytearray)
    assert summary == [{"id": "chess", "version": "1.2.0", "status": "ready"}]


def test_load_game_list_bad_preview_still_includes_game_and_summary(monkeypatch, tmp_path):
    """Corrupt base64 in preview must not drop the game from list or sync summary."""
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "dart_checker"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text(
        json.dumps(
            {
                "id": "dart_checker",
                "name": "Dart Checker",
                "type": "game",
                "version": "2.0.0",
                "preview": ["!!!not-valid-base64!!!"],
            }
        ),
        encoding="utf-8",
    )

    game_list = gl.load_game_list()
    summary = gl.get_games_summary()

    assert len(game_list) == 1
    assert game_list[0]["id"] == "dart_checker"
    assert game_list[0]["version"] == "2.0.0"
    assert isinstance(game_list[0]["preview"][0], bytearray)
    assert len(game_list[0]["preview"][0]) == 128 * 160 * 3
    assert summary == [{"id": "dart_checker", "version": "2.0.0", "status": "ready"}]


def test_bad_preview_decode_warning_is_deduped(caplog):
    gl._bad_preview_warning_keys.clear()

    with caplog.at_level(logging.DEBUG, logger=gl.__name__):
        gl._decode_game_preview_frames(["!!!not-valid-base64!!!"], "dart_checker")
        gl._decode_game_preview_frames(["!!!not-valid-base64!!!"], "dart_checker")

    messages = [
        record.getMessage()
        for record in caplog.records
        if "Skipping bad preview frame for dart_checker" in record.getMessage()
    ]
    warning_count = sum(1 for record in caplog.records if record.levelno == logging.WARNING)
    debug_count = sum(1 for record in caplog.records if record.levelno == logging.DEBUG)

    assert len(messages) == 2
    assert warning_count == 1
    assert debug_count == 1


def test_local_game_index_maps_valid_game_ids_to_folders(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for gid in ("local-a", "local-b"):
        app_dir = tmp_path / "apps" / gid
        app_dir.mkdir(parents=True)
        (app_dir / "conf.json").write_text(
            json.dumps({"id": gid, "name": gid, "type": "game", "version": "1.0.0"}),
            encoding="utf-8",
        )
    widget_dir = tmp_path / "apps" / "widget"
    widget_dir.mkdir()
    (widget_dir / "conf.json").write_text(
        json.dumps({"id": "widget", "type": "widget"}),
        encoding="utf-8",
    )

    index = gl.local_game_index()

    assert set(index) == {"local-a", "local-b"}
    assert index["local-a"] == os.path.join(str(tmp_path), "apps", "local-a")


def test_remove_local_game_folder_deletes_only_indexed_game(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    game_dir = tmp_path / "apps" / "remove-me"
    game_dir.mkdir(parents=True)
    (game_dir / "conf.json").write_text(
        json.dumps({"id": "remove-me", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )
    other_dir = tmp_path / "apps" / "keep-me"
    other_dir.mkdir()
    (other_dir / "conf.json").write_text(
        json.dumps({"id": "keep-me", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )

    assert gl.remove_local_game_folder("remove-me") is True
    assert not game_dir.exists()
    assert other_dir.exists()
    assert gl.remove_local_game_folder("../keep-me") is False
    assert other_dir.exists()


def test_get_local_game_version_returns_empty_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "broken"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text("{invalid", encoding="utf-8")

    assert gl.get_local_game_version("broken") == ""
