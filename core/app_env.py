"""Per-app virtualenv setup and launch helpers for games and widgets."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from core.helpers import app_dir, uv_bin
from core.retry import retry_with_backoff, FAST_BACKOFF_SECONDS

_log = logging.getLogger(__name__)

DEFAULTS_DIR = Path(__file__).resolve().parent / "app_defaults"
STAMP_FILENAME = ".dartsnut_stamp"
MANAGED_PYPROJECT_HEADER = "# Dartsnut managed default app dependencies"


def read_app_type(app_id: str) -> str | None:
    """Return conf.json 'type' for app_id, or None if unreadable."""
    conf_path = os.path.join(app_dir(app_id), "conf.json")
    if not os.path.isfile(conf_path):
        return None
    try:
        with open(conf_path, encoding="utf-8") as f:
            data = json.load(f)
        app_type = data.get("type")
        return str(app_type) if app_type else None
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("Failed to read conf.json for %s: %s", app_id, e)
        return None


def _read_conf_version(app_id: str) -> str:
    conf_path = os.path.join(app_dir(app_id), "conf.json")
    try:
        with open(conf_path, encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get("version") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def _template_path(app_type: str) -> Path:
    kind = app_type if app_type in ("game", "widget") else "widget"
    path = DEFAULTS_DIR / f"{kind}_pyproject.toml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing default template: {path}")
    return path


def _pyproject_path(app_id: str) -> str:
    return os.path.join(app_dir(app_id), "pyproject.toml")


def _venv_python(app_id: str) -> str:
    return os.path.join(app_dir(app_id), ".venv", "bin", "python")


def _stamp_path(app_id: str) -> str:
    return os.path.join(app_dir(app_id), ".venv", STAMP_FILENAME)


def _stamp_payload(app_id: str) -> str:
    pyproject = _pyproject_path(app_id)
    content = ""
    if os.path.isfile(pyproject):
        with open(pyproject, encoding="utf-8") as f:
            content = f.read()
    template = _template_path(read_app_type(app_id) or "widget")
    template_fp = template.read_text(encoding="utf-8")
    version = _read_conf_version(app_id)
    return f"{content}\n---\n{template_fp}\n---\n{version}"


def _compute_stamp(app_id: str) -> str:
    return hashlib.sha256(_stamp_payload(app_id).encode("utf-8")).hexdigest()


def app_venv_ready(app_id: str) -> bool:
    """True when app venv python exists and stamp matches current deps/version."""
    python_path = _venv_python(app_id)
    stamp_path = _stamp_path(app_id)
    if not os.path.isfile(python_path) or not os.path.isfile(stamp_path):
        return False
    try:
        with open(stamp_path, encoding="utf-8") as f:
            stored = f.read().strip()
        return stored == _compute_stamp(app_id)
    except OSError:
        return False


def _is_managed_default_pyproject(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            return f.readline().startswith(MANAGED_PYPROJECT_HEADER)
    except OSError:
        return False


def _materialize_pyproject(app_id: str, app_type: str) -> None:
    dest = _pyproject_path(app_id)
    template = _template_path(app_type)
    template_text = template.read_text(encoding="utf-8")
    if os.path.isfile(dest):
        if not _is_managed_default_pyproject(dest):
            return
        with open(dest, encoding="utf-8") as f:
            if f.read() == template_text:
                return
        shutil.copy2(template, dest)
        _log.info("Refreshed default pyproject.toml for %s (type=%s)", app_id, app_type)
        return
    shutil.copy2(template, dest)
    _log.info("Materialized default pyproject.toml for %s (type=%s)", app_id, app_type)


def _write_stamp(app_id: str) -> None:
    stamp_path = _stamp_path(app_id)
    os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
    with open(stamp_path, "w", encoding="utf-8") as f:
        f.write(_compute_stamp(app_id))


def _uv_sync(app_id: str) -> None:
    directory = app_dir(app_id)
    retry_with_backoff(
        lambda: subprocess.run(
            [uv_bin(), "sync", "--directory", directory],
            check=True,
            capture_output=True,
            text=True,
        ),
        succeeded=lambda _result: True,
        reraise=True,
        label=f"uv sync {app_id}",
        backoff=FAST_BACKOFF_SECONDS,
    )


def ensure_app_venv(app_id: str, *, force: bool = False) -> bool:
    """Create or refresh apps/<id>/.venv. Returns False on failure."""
    if not app_id:
        return False
    main_py = os.path.join(app_dir(app_id), "main.py")
    if not os.path.isfile(main_py):
        _log.warning("ensure_app_venv: missing main.py for %s", app_id)
        return False

    if not force and app_venv_ready(app_id):
        return True

    app_type = read_app_type(app_id) or "widget"
    started = time.monotonic()
    try:
        _materialize_pyproject(app_id, app_type)
        _uv_sync(app_id)
        _write_stamp(app_id)
        _log.info(
            "ensure_app_venv: ready app_id=%s type=%s elapsed=%.1fs",
            app_id,
            app_type,
            time.monotonic() - started,
        )
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        _log.error(
            "ensure_app_venv: uv sync failed app_id=%s: %s%s",
            app_id,
            e,
            f" stderr={stderr}" if stderr else "",
        )
        return False
    except Exception as e:
        _log.error("ensure_app_venv: failed app_id=%s: %s", app_id, e)
        return False


def infer_app_id_from_tarball(tar_path: str) -> str | None:
    """Return top-level directory name from a .tar.gz (expected app id)."""
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/")
                if parts and parts[0] and parts[0] not in (".", ".."):
                    return parts[0]
    except (OSError, tarfile.TarError) as e:
        _log.warning("infer_app_id_from_tarball: %s: %s", tar_path, e)
    return None


def infer_app_id_from_url(url: str) -> str | None:
    """Best-effort app id from download URL filename (e.g. mygame.tar.gz -> mygame)."""
    if not url:
        return None
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".tar.gz"):
        return name[: -len(".tar.gz")]
    if name.endswith(".tgz"):
        return name[: -len(".tgz")]
    return None


def ensure_app_venv_after_extract(
    tar_path: str | None,
    url: str | None = None,
    app_id: str | None = None,
) -> bool:
    """Set up venv after tarball extract; returns True if venv is ready."""
    resolved = app_id or (infer_app_id_from_tarball(tar_path) if tar_path else None)
    if not resolved and url:
        resolved = infer_app_id_from_url(url)
    if not resolved:
        _log.warning("ensure_app_venv_after_extract: could not determine app id")
        return False
    return ensure_app_venv(resolved)
