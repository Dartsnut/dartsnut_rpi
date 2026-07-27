"""Small cross-process status file used by the default connection-QR widget."""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

QR_STATUS_PATH = "/tmp/dartsnut_qr_status.json"


def write_qr_status(
    connected: bool,
    device_id: str = "",
    *,
    path: str = QR_STATUS_PATH,
) -> None:
    """Atomically publish current Supabase QR state for widget subprocesses."""
    payload = {
        "supabase_connected": bool(connected),
        "device_id": str(device_id or "").strip(),
    }
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".dartsnut_qr_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass


def read_qr_status(*, path: str = QR_STATUS_PATH) -> dict[str, Any]:
    """Read QR status, returning a disconnected default when unavailable."""
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError("QR status is not an object")
        return {
            "supabase_connected": bool(payload.get("supabase_connected", False)),
            "device_id": str(payload.get("device_id") or "").strip(),
        }
    except Exception:
        return {"supabase_connected": False, "device_id": ""}
