import json
import subprocess
import tarfile

import core.app_env as app_env


def test_read_app_type(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "mygame"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "mygame", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )
    assert app_env.read_app_type("mygame") == "game"


def test_materialize_game_pyproject_and_stamp(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "chess"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "chess", "type": "game", "version": "2.0.0"}),
        encoding="utf-8",
    )

    sync_calls = []

    def _fake_sync(app_id):
        sync_calls.append(app_id)
        venv = app_dir / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")

    monkeypatch.setattr(app_env, "_uv_sync", _fake_sync)

    assert app_env.ensure_app_venv("chess") is True
    assert (app_dir / "pyproject.toml").is_file()
    assert sync_calls == ["chess"]
    assert app_env.app_venv_ready("chess") is True

    (app_dir / "conf.json").write_text(
        json.dumps({"id": "chess", "type": "game", "version": "3.0.0"}),
        encoding="utf-8",
    )
    assert app_env.app_venv_ready("chess") is False


def test_app_python_command_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from core.helpers import app_python_command

    cmd = app_python_command("w1", "main.py", "--shm", "x")
    assert cmd[0].endswith("apps/w1/.venv/bin/python")
    assert cmd[1:] == ["main.py", "--shm", "x"]


def test_materialize_refreshes_managed_pyproject_when_template_changes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "clock"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "clock", "type": "widget", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (app_dir / "pyproject.toml").write_text(
        "# Dartsnut managed default app dependencies.\n"
        'dependencies = ["old==1.0.0"]\n',
        encoding="utf-8",
    )

    sync_calls = []

    def _fake_sync(app_id):
        sync_calls.append(app_id)
        venv = app_dir / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")

    monkeypatch.setattr(app_env, "_uv_sync", _fake_sync)
    assert app_env.ensure_app_venv("clock") is True
    assert sync_calls == ["clock"]
    refreshed = (app_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert "pydartsnut==" in refreshed
    assert "aiohttp==" in refreshed


def test_ensure_app_venv_uv_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "broken"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "broken", "type": "widget", "version": "1.0.0"}),
        encoding="utf-8",
    )

    def _fail_sync(_app_id):
        raise subprocess.CalledProcessError(1, "uv", stderr="boom")

    monkeypatch.setattr(app_env, "_uv_sync", _fail_sync)
    assert app_env.ensure_app_venv("broken") is False


def test_ensure_app_venv_removes_partial_venv_before_sync(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "factory_tool"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "factory_tool", "type": "widget", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (app_dir / "pyproject.toml").write_text(
        '[project]\nname = "factory-tool"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    partial_venv = app_dir / ".venv"
    partial_venv.mkdir()
    (partial_venv / "pyvenv.cfg").write_text("broken\n", encoding="utf-8")

    def _fake_sync(app_id):
        assert app_id == "factory_tool"
        assert not partial_venv.exists()
        venv = app_dir / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")

    monkeypatch.setattr(app_env, "_uv_sync", _fake_sync)

    assert app_env.ensure_app_venv("factory_tool") is True
    assert app_env.app_venv_ready("factory_tool") is True


def test_uv_sync_retries_then_gives_up(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "broken"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "broken", "type": "widget", "version": "1.0.0"}),
        encoding="utf-8",
    )

    import core.retry as retry

    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)
    attempts = {"n": 0}

    def _always_fail(cmd, **kwargs):
        attempts["n"] += 1
        raise subprocess.CalledProcessError(1, "uv", stderr="boom")

    monkeypatch.setattr(app_env.subprocess, "run", _always_fail)
    assert app_env.ensure_app_venv("broken") is False
    assert attempts["n"] == len(app_env.FAST_BACKOFF_SECONDS) + 1


def test_uv_sync_retries_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "flaky"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "flaky", "type": "widget", "version": "1.0.0"}),
        encoding="utf-8",
    )

    import core.retry as retry

    monkeypatch.setattr(retry.time, "sleep", lambda _s: None)
    attempts = {"n": 0}

    def _flaky(cmd, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise subprocess.CalledProcessError(1, "uv", stderr="boom")
        venv = app_dir / ".venv" / "bin"
        venv.mkdir(parents=True, exist_ok=True)
        (venv / "python").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_env.subprocess, "run", _flaky)
    assert app_env.ensure_app_venv("flaky") is True
    assert attempts["n"] == 3


def test_infer_app_id_from_tarball(tmp_path):
    tar_path = tmp_path / "dart_checker.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        import io

        data = b"print('hi')\n"
        info = tarfile.TarInfo(name="dart_checker/main.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    assert app_env.infer_app_id_from_tarball(str(tar_path)) == "dart_checker"
    assert app_env.infer_app_id_from_url("https://cdn.example.com/beerpong.tar.gz") == "beerpong"


def test_ensure_app_venv_after_extract_uses_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ensured = []

    monkeypatch.setattr(
        app_env,
        "ensure_app_venv",
        lambda app_id, force=False: ensured.append(app_id) or True,
    )
    assert app_env.ensure_app_venv_after_extract(None, url="https://x/y/mywidget.tar.gz")
    assert ensured == ["mywidget"]
