"""Persist and read the remote API token used for Dartsnut API requests."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional


def _token_dir() -> str:
    override = os.environ.get("DARTSNUT_API_TOKEN_PATH")
    if override:
        return os.path.dirname(os.path.abspath(override))
    return os.path.join(os.path.expanduser("~"), ".dartsnut")


def token_file_path() -> str:
    override = os.environ.get("DARTSNUT_API_TOKEN_PATH")
    if override:
        return os.path.abspath(override)
    return os.path.join(_token_dir(), "api_token.json")


def delete_api_token_file() -> None:
    try:
        os.remove(token_file_path())
    except FileNotFoundError:
        return
    except Exception:
        return


def _write_token(token: str) -> None:
    os.makedirs(_token_dir(), exist_ok=True)
    path = token_file_path()
    fd, tmp_path = tempfile.mkstemp(
        prefix=".api_token.",
        suffix=".tmp",
        dir=_token_dir(),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"token": token}, f)
        try:
            os.chmod(tmp_path, 0o600)
        except Exception:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def preserve_remote_user_token(user_obj: Any) -> None:
    """Persist user.token updates; explicit empty/null tokens delete the file."""
    if not isinstance(user_obj, dict) or "token" not in user_obj:
        return
    raw = user_obj.get("token")
    if raw is None:
        delete_api_token_file()
        return
    if not isinstance(raw, str):
        return
    token = raw.strip()
    if not token:
        delete_api_token_file()
        return
    _write_token(token)


def get_api_token() -> str:
    try:
        with open(token_file_path(), "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return ""
        token = payload.get("token")
        if not isinstance(token, str):
            return ""
        return token.strip()
    except Exception:
        return ""


def build_api_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers: Dict[str, str] = dict(extra or {})
    token = get_api_token()
    if token:
        headers["Token"] = token
    return headers
