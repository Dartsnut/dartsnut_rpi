"""Per-app virtualenv setup and launch helpers for games and widgets."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from core.helpers import app_dir, uv_bin
from core.app_metadata import resolve_app_metadata
from core.retry import retry_with_backoff, FAST_BACKOFF_SECONDS
from update_repair import mark_update_repair_pending, request_forcefsck

_log = logging.getLogger(__name__)

DEFAULTS_DIR = Path(__file__).resolve().parent / "app_defaults"
STAMP_FILENAME = ".dartsnut_stamp"
STAMP_VERSION = "v2:"
FAILED_SETUP_FILENAME = ".dartsnut_venv_failed"


def read_app_type(app_id: str) -> str | None:
    """Return canonical sidecar type for app_id, or None when unavailable."""
    metadata = resolve_app_metadata(app_id)
    app_type = metadata.get("type")
    return app_type if app_type in ("game", "widget") else None


def _read_backend_version(app_id: str) -> str:
    """Return sidecar version; empty string when sidecar is invalid/missing."""
    return str(resolve_app_metadata(app_id).get("version") or "")


def _template_path(app_type: str) -> Path:
    if app_type not in ("game", "widget"):
        raise ValueError(f"Invalid app type: {app_type!r}")
    path = DEFAULTS_DIR / f"{app_type}_pyproject.toml"
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


def _failed_setup_path(app_id: str) -> str:
    return os.path.join(app_dir(app_id), FAILED_SETUP_FILENAME)


def app_venv_setup_failed(app_id: str) -> bool:
    """True when latest download/update could not prepare its virtualenv."""
    return os.path.isfile(_failed_setup_path(app_id))


def _mark_app_venv_setup_failed(app_id: str) -> None:
    try:
        Path(_failed_setup_path(app_id)).touch()
    except OSError:
        pass


def _clear_app_venv_setup_failed(app_id: str) -> None:
    try:
        os.remove(_failed_setup_path(app_id))
    except FileNotFoundError:
        pass


def _stamp_payload(app_id: str) -> str:
    pyproject = _pyproject_path(app_id)
    content = ""
    if os.path.isfile(pyproject):
        with open(pyproject, encoding="utf-8") as f:
            content = f.read()
    app_type = read_app_type(app_id)
    if app_type is None:
        raise ValueError(f"Missing or invalid backend metadata for {app_id!r}")
    version = _read_backend_version(app_id)
    return f"{content}\n---\n{app_type}\n---\n{version}"


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
        if stored.startswith(STAMP_VERSION):
            return stored == f"{STAMP_VERSION}{_compute_stamp(app_id)}"
        # Pre-v2 stamps included firmware template contents. Their hash cannot
        # be reproduced after a device upgrade, but the existing venv remains
        # valid until the next app download/update replaces it with a v2 stamp.
        return len(stored) == 64 and all(char in "0123456789abcdef" for char in stored)
    except (OSError, ValueError):
        return False


def _materialize_pyproject(app_id: str, app_type: str) -> None:
    dest = _pyproject_path(app_id)
    if os.path.lexists(dest):
        return
    template_content = _template_path(app_type).read_bytes()
    try:
        with open(dest, "xb") as f:
            f.write(template_content)
    except FileExistsError:
        return
    _log.info("Materialized default pyproject.toml for %s (type=%s)", app_id, app_type)


def _write_stamp(app_id: str) -> None:
    stamp_path = _stamp_path(app_id)
    os.makedirs(os.path.dirname(stamp_path), exist_ok=True)
    with open(stamp_path, "w", encoding="utf-8") as f:
        f.write(f"{STAMP_VERSION}{_compute_stamp(app_id)}")


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
        _mark_app_venv_setup_failed(app_id)
        return False

    if not force and app_venv_ready(app_id):
        _clear_app_venv_setup_failed(app_id)
        return True

    app_type = read_app_type(app_id)
    if app_type is None:
        _log.warning("ensure_app_venv: missing or invalid backend metadata for %s", app_id)
        _mark_app_venv_setup_failed(app_id)
        return False
    started = time.monotonic()
    try:
        _materialize_pyproject(app_id, app_type)
        _remove_invalid_venv(app_id)
        _uv_sync(app_id)
        _write_stamp(app_id)
        _clear_app_venv_setup_failed(app_id)
        _log.info(
            "ensure_app_venv: ready app_id=%s type=%s elapsed=%.1fs",
            app_id,
            app_type,
            time.monotonic() - started,
        )
        return True
    except subprocess.CalledProcessError as e:
        _mark_app_venv_setup_failed(app_id)
        stderr = (e.stderr or "").strip()
        _log.error(
            "ensure_app_venv: uv sync failed app_id=%s: %s%s",
            app_id,
            e,
            f" stderr={stderr}" if stderr else "",
        )
        return False
    except Exception as e:
        _mark_app_venv_setup_failed(app_id)
        _log.error("ensure_app_venv: failed app_id=%s: %s", app_id, e)
        return False


def ensure_sideload_app_venv(app_path: str, app_type: str) -> bool:
    """Prepare a local sideload app without requiring backend metadata."""
    if not app_path or app_type not in ("game", "widget"):
        return False
    main_py = os.path.join(app_path, "main.py")
    if not os.path.isfile(main_py):
        return False
    python_path = os.path.join(app_path, ".venv", "bin", "python")
    started = time.monotonic()
    try:
        pyproject = os.path.join(app_path, "pyproject.toml")
        if not os.path.isfile(pyproject):
            shutil.copy2(_template_path(app_type), pyproject)
        venv_path = os.path.join(app_path, ".venv")
        if os.path.exists(venv_path) and not os.path.isfile(python_path):
            shutil.rmtree(venv_path)
        _run_uv_sync_command(app_path)
        ready = os.path.isfile(python_path)
        if ready:
            _log.info(
                "ensure_sideload_app_venv: ready app=%s type=%s elapsed=%.1fs",
                os.path.basename(app_path),
                app_type,
                time.monotonic() - started,
            )
        return ready
    except Exception as exc:
        _log.error("ensure_sideload_app_venv: failed app=%s: %s", app_path, exc)
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
        shutil.copytree(
            source_dir,
            prepared_dir,
            ignore=shutil.ignore_patterns("._*", "__MACOSX"),
        )

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
