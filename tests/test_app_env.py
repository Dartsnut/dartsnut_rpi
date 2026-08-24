import json
import io
import subprocess
import tarfile

import pytest

import core.app_env as app_env
from core.app_metadata import write_app_metadata


def _write_backend_metadata(app_id, app_type, version="1.0.0"):
    write_app_metadata(
        app_id,
        {"id": app_id, "type": app_type, "version": version},
    )


def test_read_app_type(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "mygame"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "mygame", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _write_backend_metadata("mygame", "game")
    assert app_env.read_app_type("mygame") == "game"


def test_read_app_type_ignores_conf_without_backend_sidecar(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "legacy"
    app_dir.mkdir(parents=True)
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "legacy", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )
    assert app_env.read_app_type("legacy") is None


def test_materialize_game_pyproject_and_stamp(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "chess"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "chess", "type": "game", "version": "2.0.0"}),
        encoding="utf-8",
    )
    _write_backend_metadata("chess", "game", "2.0.0")

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
    _write_backend_metadata("chess", "game", "3.0.0")
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
    _write_backend_metadata("clock", "widget")
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
    _write_backend_metadata("broken", "widget")

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
    _write_backend_metadata("factory_tool", "widget")
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
    _write_backend_metadata("broken", "widget")

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
    _write_backend_metadata("flaky", "widget")

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


def test_uv_sync_cleans_root_cache_for_app_venvs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "cache_busted"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "cache_busted", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _write_backend_metadata("cache_busted", "game")

    commands = []

    def _run(cmd, **kwargs):
        commands.append(cmd)
        if cmd == [app_env.uv_bin(), "cache", "clean", "--force"]:
            return subprocess.CompletedProcess(cmd, 0)
        if len(commands) == 1:
            raise subprocess.CalledProcessError(
                2,
                cmd,
                stderr=(
                    "error: Failed to write to the client cache\n"
                    "  Caused by: failed to create directory "
                    "`/root/.cache/uv/simple-v21/pypi`: Bad message (os error 74)"
                ),
            )
        venv = app_dir / ".venv" / "bin"
        venv.mkdir(parents=True, exist_ok=True)
        (venv / "python").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_env.subprocess, "run", _run)
    assert app_env.ensure_app_venv("cache_busted") is True
    assert commands == [
        [
            app_env.uv_bin(),
            "sync",
            "--directory",
            str(app_dir),
        ],
        [app_env.uv_bin(), "cache", "clean", "--force"],
        [
            app_env.uv_bin(),
            "sync",
            "--directory",
            str(app_dir),
        ],
    ]


def test_uv_sync_requests_forcefsck_when_cache_clean_finds_fs_corruption(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "cache_fsck"
    app_dir.mkdir(parents=True)
    (app_dir / "main.py").write_text("pass\n", encoding="utf-8")
    (app_dir / "conf.json").write_text(
        json.dumps({"id": "cache_fsck", "type": "game", "version": "1.0.0"}),
        encoding="utf-8",
    )
    _write_backend_metadata("cache_fsck", "game")

    commands = []
    repair_markers = []

    def _run(cmd, **kwargs):
        commands.append(cmd)
        if cmd == [app_env.uv_bin(), "cache", "clean", "--force"]:
            raise subprocess.CalledProcessError(
                117,
                cmd,
                stderr=(
                    "error: Failed to clear cache at: /root/.cache/uv\n"
                    "  Caused by: Structure needs cleaning (os error 117)"
                ),
            )
        raise subprocess.CalledProcessError(
            2,
            cmd,
            stderr=(
                "error: Failed to write to the client cache\n"
                "  Caused by: failed to create directory "
                "`/root/.cache/uv/simple-v21/pypi`: Bad message (os error 74)"
            ),
        )

    monkeypatch.setattr(app_env.subprocess, "run", _run)
    monkeypatch.setattr(
        app_env,
        "mark_update_repair_pending",
        lambda: repair_markers.append("mark"),
    )
    monkeypatch.setattr(
        app_env,
        "request_forcefsck",
        lambda: repair_markers.append("forcefsck"),
    )

    assert app_env.ensure_app_venv("cache_fsck") is False
    assert repair_markers == ["mark", "forcefsck"] * 3
    assert commands == [
        [
            app_env.uv_bin(),
            "sync",
            "--directory",
            str(app_dir),
        ],
        [app_env.uv_bin(), "cache", "clean", "--force"],
        [
            app_env.uv_bin(),
            "sync",
            "--directory",
            str(app_dir),
        ],
        [app_env.uv_bin(), "cache", "clean", "--force"],
        [
            app_env.uv_bin(),
            "sync",
            "--directory",
            str(app_dir),
        ],
        [app_env.uv_bin(), "cache", "clean", "--force"],
    ]


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


def _write_tarball(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_install_app_tarball_uses_game_id_not_archive_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    tar_path = tmp_path / "downloads" / "random-name.tar.gz"
    tar_path.parent.mkdir()
    _write_tarball(
        tar_path,
        {
            "wrong-folder/conf.json": json.dumps({"id": "wrong-folder", "type": "game"}),
            "wrong-folder/main.py": "print('ok')\n",
        },
    )

    app_env.install_app_tarball(str(tar_path), "chess")

    assert (tmp_path / "apps" / "chess" / "conf.json").is_file()
    assert (tmp_path / "apps" / "chess" / "main.py").is_file()
    assert not (tmp_path / "apps" / "wrong-folder").exists()


def test_install_app_tarball_supports_flat_archive(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    tar_path = tmp_path / "flat.tar.gz"
    _write_tarball(
        tar_path,
        {
            "conf.json": json.dumps({"id": "flat-source", "type": "game"}),
            "main.py": "print('ok')\n",
        },
    )

    app_env.install_app_tarball(str(tar_path), "flat-game")

    assert (tmp_path / "apps" / "flat-game" / "conf.json").is_file()
    assert (tmp_path / "apps" / "flat-game" / "main.py").is_file()


def test_install_app_tarball_rejects_unsafe_members_without_replacing_existing_app(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "apps" / "chess"
    existing.mkdir(parents=True)
    (existing / "main.py").write_text("print('old')\n", encoding="utf-8")
    tar_path = tmp_path / "unsafe.tar.gz"
    _write_tarball(tar_path, {"../evil.txt": "bad"})

    with pytest.raises(ValueError):
        app_env.install_app_tarball(str(tar_path), "chess")

    assert (existing / "main.py").read_text(encoding="utf-8") == "print('old')\n"
    assert not (tmp_path / "evil.txt").exists()


def test_install_app_tarball_preserves_embedded_managed_pyproject(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    tar_path = tmp_path / "mathdarts.tar.gz"
    packaged_pyproject = (
        "# Dartsnut managed default app dependencies.\n"
        "[project]\n"
        'name = "mathdarts"\n'
        'version = "0.2.0"\n'
        'dependencies = ["custom-dependency==1.0.0"]\n'
    )
    _write_tarball(
        tar_path,
        {
            "mathdarts/conf.json": json.dumps(
                {"id": "mathdarts", "type": "game", "version": "0.2.0"}
            ),
            "mathdarts/main.py": "print('new')\n",
            "mathdarts/pyproject.toml": packaged_pyproject,
        },
    )

    app_env.install_app_tarball(str(tar_path), "mathdarts")

    app_dir = tmp_path / "apps" / "mathdarts"
    _write_backend_metadata("mathdarts", "game", "0.2.0")

    def _fake_sync(_app_id):
        venv = app_dir / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")

    monkeypatch.setattr(app_env, "_uv_sync", _fake_sync)
    assert app_env.ensure_app_venv("mathdarts") is True
    assert (app_dir / "pyproject.toml").read_text(encoding="utf-8") == packaged_pyproject


def test_install_app_tarball_replaces_existing_app_after_success(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "apps" / "chess"
    existing.mkdir(parents=True)
    (existing / "old.txt").write_text("old\n", encoding="utf-8")
    tar_path = tmp_path / "replacement.tar.gz"
    _write_tarball(
        tar_path,
        {
            "other-name/conf.json": json.dumps({"id": "other-name", "type": "game"}),
            "other-name/main.py": "print('new')\n",
        },
    )

    app_env.install_app_tarball(str(tar_path), "chess")

    assert not (existing / "old.txt").exists()
    assert (existing / "main.py").read_text(encoding="utf-8") == "print('new')\n"


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
