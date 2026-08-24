"""Backend-derived metadata for installed apps."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from core.helpers import app_dir

METADATA_FILENAME = ".dartsnut_backend.json"
APP_TYPES = frozenset(("game", "widget"))
_log = logging.getLogger(__name__)


def _validate_app_id(app_id: str) -> str:
    value = str(app_id or "").strip()
    if not value or value.startswith(".") or "/" in value or "\\" in value:
        raise ValueError(f"Invalid app_id: {app_id!r}")
    return value


def _metadata_path(app_id: str) -> str:
    return os.path.join(app_dir(_validate_app_id(app_id)), METADATA_FILENAME)


def validate_app_metadata(app_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize canonical backend metadata.

    Sidecar metadata is the only source of app identity, type, and version.  A
    missing or malformed ``type`` makes metadata unusable; callers must not
    infer a type from package files such as ``conf.json``.
    """
    safe_id = _validate_app_id(app_id)
    if not isinstance(metadata, dict):
        raise ValueError("App metadata must be a JSON object")

    app_type = metadata.get("type")
    if not isinstance(app_type, str) or app_type not in APP_TYPES:
        raise ValueError(f"Invalid app metadata type for {safe_id!r}: {app_type!r}")

    metadata_id = metadata.get("id")
    if not isinstance(metadata_id, str) or not metadata_id.strip():
        raise ValueError(f"Missing app metadata id for {safe_id!r}")
    # Backend IDs share same path-safe constraints as local app IDs.
    _validate_app_id(metadata_id)

    normalized: dict[str, Any] = {
        "id": metadata_id.strip(),
        "type": app_type,
        "version": str(metadata.get("version") or ""),
        "name": str(metadata.get("name") or ""),
        "preview_urls": [],
        "download_url": str(metadata.get("download_url") or ""),
        "download_md5": str(metadata.get("download_md5") or ""),
        "updated_at": str(metadata.get("updated_at") or ""),
    }
    preview_urls = metadata.get("preview_urls") or []
    if not isinstance(preview_urls, list):
        raise ValueError(f"Invalid app metadata preview_urls for {safe_id!r}")
    normalized["preview_urls"] = [str(item) for item in preview_urls if item]
    return normalized


def read_app_metadata(app_id: str) -> dict[str, Any]:
    """Return backend metadata for app_id, or {} when missing/unreadable."""
    path = _metadata_path(app_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return validate_app_metadata(app_id, data)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        if isinstance(e, ValueError):
            _log.warning("Invalid backend metadata for %s: %s", app_id, e)
        return {}


def write_app_metadata(app_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Persist canonical backend metadata for an installed app."""
    safe_id = _validate_app_id(app_id)
    target_dir = app_dir(safe_id)
    os.makedirs(target_dir, exist_ok=True)

    preview_urls = metadata.get("preview_urls") or []
    if not isinstance(preview_urls, list):
        preview_urls = []

    payload: dict[str, Any] = {
        "id": str(metadata.get("id") or safe_id),
        "type": str(metadata.get("type") or ""),
        "version": str(metadata.get("version") or ""),
        "name": str(metadata.get("name") or ""),
        "preview_urls": [str(item) for item in preview_urls if item],
        "download_url": str(metadata.get("download_url") or ""),
        "download_md5": str(metadata.get("download_md5") or ""),
        "updated_at": str(
            metadata.get("updated_at")
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ),
    }

    # Validate before touching filesystem. This prevents writing metadata that
    # later causes app classification or virtualenv setup to fall back.
    payload = validate_app_metadata(safe_id, payload)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{METADATA_FILENAME}.", suffix=".tmp", dir=target_dir, text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, os.path.join(target_dir, METADATA_FILENAME))
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return payload
