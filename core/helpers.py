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


def uv_run_python_command(script: str, *args: str) -> list[str]:
    repo_root = os.getcwd()
    return [uv_bin(), "run", "--directory", repo_root, "python", script, *args]
