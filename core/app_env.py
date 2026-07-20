"""Per-app virtualenv setup and launch helpers for games and widgets."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from core.helpers import app_dir, uv_bin
from core.retry import retry_with_backoff, FAST_BACKOFF_SECONDS
from update_repair import mark_update_repair_pending, request_forcefsck

_log = logging.getLogger(__name__)

DEFAULTS_DIR = Path(__file__).resolve().parent / "app_defaults"
STAMP_FILENAME = ".dartsnut_stamp"
MANAGED_PYPROJECT_HEADER = "# Dartsnut managed default app dependencies"
TARBALL_PYPROJECT_MARKER = ".dartsnut_tarball_pyproject"


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


def _venv_dir(app_id: str) -> str:
    return os.path.join(app_dir(app_id), ".venv")


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
        marker = os.path.join(app_dir(app_id), TARBALL_PYPROJECT_MARKER)
        if os.path.isfile(marker):
            return
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


def _stderr_has_uv_root_cache_error(stderr: str | None) -> bool:
    text = stderr or ""
    return "Failed to write to the client cache" in text or "Bad message (os error 74)" in text


def _stderr_has_filesystem_corruption(stderr: str | None) -> bool:
    text = stderr or ""
    return "Structure needs cleaning" in text or "os error 117" in text


def _run_uv_sync_command(directory: str) -> subprocess.CompletedProcess[str]:
    cmd = [uv_bin(), "sync", "--directory", directory]
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        if not _stderr_has_uv_root_cache_error(e.stderr):
            raise
        _log.warning("uv cache appears corrupt; cleaning root uv cache and retrying app sync")
        try:
            subprocess.run(
                [uv_bin(), "cache", "clean", "--force"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as clean_error:
            if _stderr_has_filesystem_corruption(clean_error.stderr):
                _log.error(
                    "uv cache clean hit filesystem corruption; requesting fsck on next boot"
                )
                mark_update_repair_pending()
                request_forcefsck()
            raise
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )


def _uv_sync(app_id: str) -> None:
    directory = app_dir(app_id)
    retry_with_backoff(
        lambda: _run_uv_sync_command(directory),
        succeeded=lambda _result: True,
        reraise=True,
        label=f"uv sync {app_id}",
        backoff=FAST_BACKOFF_SECONDS,
    )


def _remove_invalid_venv(app_id: str) -> None:
    """Remove a partial app venv that uv refuses to repair in place."""
    venv_path = _venv_dir(app_id)
    if not os.path.exists(venv_path) or os.path.isfile(_venv_python(app_id)):
        return
    if os.path.isdir(venv_path) and not os.path.islink(venv_path):
        shutil.rmtree(venv_path)
    else:
        os.remove(venv_path)
    _log.warning("Removed invalid app virtualenv for %s: %s", app_id, venv_path)


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
        _remove_invalid_venv(app_id)
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


def _validate_tar_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive member path: {member.name!r}")
    if member.issym() or member.islnk():
        raise ValueError(f"Archive links are not supported: {member.name!r}")


def _payload_source_dir(extract_dir: str) -> str:
    entries = [
        entry
        for entry in os.listdir(extract_dir)
        if entry != "__MACOSX" and not entry.startswith("._")
    ]
    if len(entries) == 1:
        only_entry = os.path.join(extract_dir, entries[0])
        if os.path.isdir(only_entry):
            return only_entry
    return extract_dir


def install_app_tarball(tar_path: str, app_id: str) -> str:
    """Extract tar_path and install its payload into apps/<app_id>."""
    if not app_id or os.path.isabs(app_id) or "/" in app_id or "\\" in app_id:
        raise ValueError(f"Invalid app_id: {app_id!r}")

    apps_dir = os.path.join(os.getcwd(), "apps")
    os.makedirs(apps_dir, exist_ok=True)
    target_dir = app_dir(app_id)

    with tempfile.TemporaryDirectory(prefix="dartsnut_app_extract_") as tmp_dir:
        extract_dir = os.path.join(tmp_dir, "extract")
        prepared_dir = os.path.join(tmp_dir, "prepared")
        os.makedirs(extract_dir)

        with tarfile.open(tar_path, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                _validate_tar_member(member)
            try:
                tar.extractall(extract_dir, members=members, filter="data")
            except TypeError:
                tar.extractall(extract_dir, members=members)

        source_dir = _payload_source_dir(extract_dir)
        tarball_has_pyproject = os.path.isfile(
            os.path.join(source_dir, "pyproject.toml")
        )
        shutil.copytree(
            source_dir,
            prepared_dir,
            ignore=shutil.ignore_patterns(
                "._*", "__MACOSX", TARBALL_PYPROJECT_MARKER
            ),
        )
        if tarball_has_pyproject:
            Path(prepared_dir, TARBALL_PYPROJECT_MARKER).touch()

        backup_dir = None
        if os.path.exists(target_dir):
            backup_dir = tempfile.mkdtemp(prefix=f".{app_id}.backup.", dir=apps_dir)
            os.rmdir(backup_dir)
            shutil.move(target_dir, backup_dir)

        try:
            shutil.move(prepared_dir, target_dir)
        except Exception:
            if backup_dir and os.path.exists(backup_dir) and not os.path.exists(target_dir):
                shutil.move(backup_dir, target_dir)
            raise
        else:
            if backup_dir and os.path.exists(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)

    return target_dir


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
