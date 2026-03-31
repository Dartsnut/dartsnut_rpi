import base64
import io
import json
import os

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
    (tmp_path / "apps" / "chess").mkdir(parents=True)
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
    monkeypatch.setattr(gl.subprocess, "Popen", lambda cmd, cwd, preexec_fn: popen_calls.append((cmd, cwd)) or _Proc())

    game = gl.start_game_process("chess")

    assert game["game_id"] == "chess"
    assert game["launched"] is False
    assert tracked == ["chess"]
    assert popen_calls and popen_calls[0][1] == "./apps/chess"
    assert isinstance(created["shm"].buf, bytearray)


def test_term_game_process_kills_running_process_and_cleans_resources(monkeypatch):
    kills = []
    stopped = []
    shm = _FakeShm("game_shm", 8)
    g = {"process": _Proc(pid=9876, running=True), "shm": shm, "game_id": "chess"}

    monkeypatch.setattr(gl, "stop_game_tracking", lambda: stopped.append(True))
    monkeypatch.setattr(gl.os, "kill", lambda pid, sig: kills.append((pid, sig)))

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


def test_get_local_game_version_returns_empty_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "broken"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text("{invalid", encoding="utf-8")

    assert gl.get_local_game_version("broken") == ""
