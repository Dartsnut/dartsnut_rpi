"""Shared helpers for widget/game lifecycle (user data path, process death signal)."""
import logging
import os
import signal
from python_websocket.user_data_operations import _load_user_data

_log = logging.getLogger(__name__)


def set_pdeathsig():
    """Set PR_SET_PDEATHSIG so child processes get SIGKILL when parent dies."""
    import ctypes
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)


def signal_process_group(pid: int, sig: int) -> None:
    """Signal a subprocess and its children."""
    if pid <= 0:
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        if pgid == os.getpgrp():
            os.kill(pid, sig)
        else:
            os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def terminate_process_group(pid: int) -> None:
    """Force-stop a subprocess tree (app python or uv run wrapper)."""
    signal_process_group(pid, signal.SIGCONT)
    signal_process_group(pid, signal.SIGKILL)


def subprocess_launch_kwargs() -> dict:
    """Common Popen options for app subprocesses."""
    return {"start_new_session": True, "preexec_fn": set_pdeathsig}


def get_user_data_store_path(app_id: str) -> str:
    """Return user data store path for app_id; default to 'guest' if user_id empty."""
    try:
        user_data = _load_user_data()
        user_id = user_data.get("user_id", "") or "guest"
        path = f"/var/lib/dartsnut/user/{user_id}/{app_id}/"
        os.makedirs(path, mode=0o755, exist_ok=True)
        return path
    except Exception as e:
        _log.warning("Failed to load user data, defaulting to guest: %s", e)
        path = f"/var/lib/dartsnut/user/guest/{app_id}/"
        os.makedirs(path, mode=0o755, exist_ok=True)
        return path


def uv_bin() -> str:
    return os.environ.get("DARTSNUT_UV_BIN", "/root/.local/bin/uv")


def repo_root() -> str:
    return os.getcwd()


def app_dir(app_id: str) -> str:
    return os.path.join(repo_root(), "apps", app_id)


def uv_run_script_command(script_relpath: str, *args: str) -> list[str]:
    """Build argv for `uv run <script>` with Popen cwd at repo root."""
    return [uv_bin(), "run", script_relpath, *args]


def app_python_executable(app_id: str) -> str:
    """Path to apps/<id>/.venv/bin/python (caller should ensure_app_venv first)."""
    return os.path.join(app_dir(app_id), ".venv", "bin", "python")


def app_python_command(app_id: str, script: str, *args: str) -> list[str]:
    """Build argv using the app-local venv interpreter."""
    return [app_python_executable(app_id), script, *args]


def uv_run_app_command(app_id: str, script: str, *args: str) -> list[str]:
    """Deprecated: use app_python_command after ensure_app_venv."""
    return app_python_command(app_id, script, *args)
