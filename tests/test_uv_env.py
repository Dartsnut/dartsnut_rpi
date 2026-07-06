import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
UV_ENV_SCRIPT = REPO_ROOT / "scripts" / "uv_env.sh"
UPDATE_SCRIPT = REPO_ROOT / "update.sh"
SETUP_SCRIPT = REPO_ROOT / "setup.sh"


def _write_fake_project(tmp_path: Path, uv_script: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                "dependencies = [",
                '    "pygame-ce==2.5.7",',
                '    "pybluez-dartsnut==0.30",',
                "]",
            ]
        ),
        encoding="utf-8",
    )
    uv = repo / "uv"
    uv.write_text(uv_script, encoding="utf-8")
    uv.chmod(0o755)
    return repo


def _run_refresh(repo: Path, log: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f'sleep() {{ printf "sleep %s\\n" "$1" >> "$UV_CALL_LOG"; }}; source "{UV_ENV_SCRIPT}"; refresh_uv_project',
        ],
        cwd=repo,
        env={
            **os.environ,
            "REPO_DIR": str(repo),
            "UV_CALL_LOG": str(log),
            "UV_SYNC_MAX_ATTEMPTS": "3",
        },
        text=True,
        capture_output=True,
    )


def test_refresh_uv_project_cleans_corrupt_wheel_cache_and_retries(tmp_path: Path) -> None:
    repo = _write_fake_project(
        tmp_path,
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
if [ "$1" = "sync" ]; then
    if [ ! -f "$REPO_DIR/sync_failed_once" ]; then
        touch "$REPO_DIR/sync_failed_once"
        echo 'error: Failed to install: pygobject-3.50.2-cp313-cp313-linux_aarch64.whl (pygobject==3.50.2)' >&2
        echo '  Caused by: The wheel is invalid: Metadata field Name not found' >&2
        exit 1
    fi
    [ "$2" = "--inexact" ] && [ "$3" = "--refresh-package" ] && [ "$4" = "pygobject" ] && exit 0
    exit 2
fi
[ "$1" = "venv" ] && [ "$2" = "--system-site-packages" ] && exit 0
[ "$1" = "cache" ] && [ "$2" = "clean" ] && [ "$3" = "pygobject" ] && exit 0
[ "$1" = "run" ] && exit 0
[ "$1" = "pip" ] && exit 0
exit 3
""",
    )
    log = tmp_path / "uv.log"

    result = _run_refresh(repo, log)

    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "venv --system-site-packages",
        "sync --inexact",
        "cache clean pygobject",
        "sleep 2",
        "sync --inexact --refresh-package pygobject",
        "run python -c import bluezero",
        "run python -c import pygame; pygame.Surface",
        "run python -c import bluetooth; import bluetooth._bluetooth",
        "run python -c import dbus",
        "run python -c import gi",
    ]


def test_refresh_uv_project_retries_transient_sync_failures_with_backoff(
    tmp_path: Path,
) -> None:
    repo = _write_fake_project(
        tmp_path,
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
if [ "$1" = "sync" ]; then
    count_file="$REPO_DIR/sync_count"
    count=0
    [ -f "$count_file" ] && count="$(cat "$count_file")"
    count=$((count + 1))
    printf '%s' "$count" > "$count_file"
    [ "$count" -lt 3 ] && echo 'error: network down' >&2 && exit 1
    exit 0
fi
[ "$1" = "venv" ] && [ "$2" = "--system-site-packages" ] && exit 0
[ "$1" = "run" ] && exit 0
[ "$1" = "pip" ] && exit 0
exit 3
""",
    )
    log = tmp_path / "uv.log"

    result = _run_refresh(repo, log)

    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "venv --system-site-packages",
        "sync --inexact",
        "sleep 2",
        "sync --inexact",
        "sleep 4",
        "sync --inexact",
        "run python -c import bluezero",
        "run python -c import pygame; pygame.Surface",
        "run python -c import bluetooth; import bluetooth._bluetooth",
        "run python -c import dbus",
        "run python -c import gi",
    ]


def test_refresh_uv_project_fails_before_verify_when_sync_retry_cannot_recover(
    tmp_path: Path,
) -> None:
    repo = _write_fake_project(
        tmp_path,
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
if [ "$1" = "sync" ]; then
    echo 'error: network down' >&2
    exit 1
fi
[ "$1" = "venv" ] && [ "$2" = "--system-site-packages" ] && exit 0
[ "$1" = "run" ] && exit 7
exit 3
""",
    )
    log = tmp_path / "uv.log"

    result = _run_refresh(repo, log)

    assert result.returncode == 1
    assert log.read_text(encoding="utf-8").splitlines() == [
        "venv --system-site-packages",
        "sync --inexact",
        "sleep 2",
        "sync --inexact",
        "sleep 4",
        "sync --inexact",
    ]


def test_verify_helpers_require_force_reinstall_success(tmp_path: Path) -> None:
    repo = _write_fake_project(
        tmp_path,
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
[ "$1" = "sync" ] && exit 0
[ "$1" = "venv" ] && [ "$2" = "--system-site-packages" ] && exit 0
if [ "$1" = "run" ]; then
    [ "$*" = "run python -c import bluezero" ] && [ -f "$REPO_DIR/bluezero_installed" ] && exit 0
    [ -f "$REPO_DIR/pygame_installed" ] && exit 0
    exit 1
fi
if [ "$1" = "pip" ]; then
    [ "$2" = "install" ] && [ "$3" = "--force-reinstall" ] && [ "$4" = "--no-deps" ] && [ "$5" = "bluezero==0.9.1" ] && touch "$REPO_DIR/bluezero_installed" && exit 0
    [ "$2" = "install" ] && [ "$3" = "--force-reinstall" ] && [ "$5" = "pygame-ce==2.5.7" ] && exit 9
fi
exit 3
""",
    )
    log = tmp_path / "uv.log"

    result = _run_refresh(repo, log)

    assert result.returncode == 9
    assert log.read_text(encoding="utf-8").splitlines() == [
        "venv --system-site-packages",
        "sync --inexact",
        "run python -c import bluezero",
        "pip install --force-reinstall --no-deps bluezero==0.9.1",
        "run python -c import bluezero",
        "run python -c import pygame; pygame.Surface",
        "pip install --force-reinstall --no-deps pygame-ce==2.5.7",
    ]


def test_refresh_uv_project_requires_system_dbus_after_sync(tmp_path: Path) -> None:
    repo = _write_fake_project(
        tmp_path,
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
[ "$1" = "venv" ] && [ "$2" = "--system-site-packages" ] && exit 0
[ "$1" = "sync" ] && exit 0
if [ "$1" = "run" ]; then
    case "$*" in
        *"import pygame"*|*"import bluetooth"*|*"import bluezero"*|*"import gi"*) exit 0 ;;
        *"import dbus"*) exit 11 ;;
    esac
fi
[ "$1" = "pip" ] && exit 0
exit 3
""",
    )
    log = tmp_path / "uv.log"

    result = _run_refresh(repo, log)

    assert result.returncode == 1
    assert "python3-dbus" in result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "venv --system-site-packages",
        "sync --inexact",
        "run python -c import bluezero",
        "run python -c import pygame; pygame.Surface",
        "run python -c import bluetooth; import bluetooth._bluetooth",
        "run python -c import dbus",
    ]


def test_refresh_uv_project_installs_bluezero_without_pygobject_deps(
    tmp_path: Path,
) -> None:
    repo = _write_fake_project(
        tmp_path,
        """#!/bin/sh
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
[ "$1" = "venv" ] && [ "$2" = "--system-site-packages" ] && exit 0
[ "$1" = "sync" ] && exit 0
if [ "$1" = "run" ]; then
    [ "$*" = "run python -c import bluezero" ] && [ ! -f "$REPO_DIR/bluezero_installed" ] && exit 1
    exit 0
fi
if [ "$1" = "pip" ]; then
    [ "$2" = "install" ] && [ "$3" = "--force-reinstall" ] && [ "$4" = "--no-deps" ] && [ "$5" = "bluezero==0.9.1" ] && touch "$REPO_DIR/bluezero_installed" && exit 0
fi
exit 3
""",
    )
    log = tmp_path / "uv.log"

    result = _run_refresh(repo, log)

    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "venv --system-site-packages",
        "sync --inexact",
        "run python -c import bluezero",
        "pip install --force-reinstall --no-deps bluezero==0.9.1",
        "run python -c import bluezero",
        "run python -c import pygame; pygame.Surface",
        "run python -c import bluetooth; import bluetooth._bluetooth",
        "run python -c import dbus",
        "run python -c import gi",
    ]


def test_update_script_stops_when_uv_refresh_fails() -> None:
    script = UPDATE_SCRIPT.read_text(encoding="utf-8")

    assert 'refresh_uv_project || exit $?' in script


def test_setup_script_stops_when_uv_setup_fails() -> None:
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'setup_uv_project || exit $?' in script


def test_native_dbus_and_gi_come_from_system_packages_not_pypi_build() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    system_packages = (REPO_ROOT / "system-packages.txt").read_text(encoding="utf-8")

    assert "dbus-python" not in pyproject
    assert "bluezero" not in pyproject
    assert "pygobject" not in lock
    assert "python3-dbus" in system_packages
    assert "python3-gi" in system_packages
