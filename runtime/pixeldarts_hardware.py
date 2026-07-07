"""Detect PIXELDARTS USB hardware revision (444e/444f) from lsusb."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess

_log = logging.getLogger(__name__)

_CACHE_FILENAME = ".hardware_version.json"
_PIXELDARTS_DEVICE_CACHE: bool | None = None
_PID_RE = re.compile(
    r"\bID\s+[0-9a-fA-F]{4}:([0-9a-fA-F]{4})\b.*\bPIXELDARTS\b",
    flags=re.IGNORECASE,
)


def _cache_path() -> str:
    return os.path.join(os.getcwd(), _CACHE_FILENAME)


def extract_pixeldarts_pid(lsusb_output: str) -> str:
    for line in (lsusb_output or "").splitlines():
        match = _PID_RE.search(line.strip())
        if match:
            return match.group(1).lower()
    return ""


def read_cached_hardware_version() -> str:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("hardware_version", "")).strip().lower()
    except Exception:
        return ""


def write_cached_hardware_version(version: str) -> None:
    value = str(version or "").strip().lower()
    if not value:
        return
    try:
        with open(_cache_path(), "w", encoding="utf-8") as file:
            json.dump({"hardware_version": value}, file)
    except Exception as exc:
        _log.debug("Failed to persist hardware cache: %s", exc)


def probe_lsusb_hardware_version() -> str:
    try:
        output = subprocess.check_output(["lsusb"]).decode("utf-8", errors="ignore")
        return extract_pixeldarts_pid(output)
    except Exception:
        return ""


def is_pixeldart_device() -> bool:
    """Return True only when live USB enumeration shows a PIXELDARTS device."""
    global _PIXELDARTS_DEVICE_CACHE
    if _PIXELDARTS_DEVICE_CACHE is not None:
        return _PIXELDARTS_DEVICE_CACHE
    try:
        output = subprocess.check_output(["lsusb"]).decode("utf-8", errors="ignore")
    except Exception:
        _PIXELDARTS_DEVICE_CACHE = False
        return _PIXELDARTS_DEVICE_CACHE
    _PIXELDARTS_DEVICE_CACHE = any(
        "PIXELDARTS" in line.upper() for line in output.splitlines()
    )
    return _PIXELDARTS_DEVICE_CACHE


def is_pixelboard_device() -> bool:
    return not is_pixeldart_device()


def clear_device_type_cache() -> None:
    global _PIXELDARTS_DEVICE_CACHE
    _PIXELDARTS_DEVICE_CACHE = None


def resolve_pixeldarts_hardware_version() -> str:
    """Prefer live lsusb over cache so stale image defaults cannot stick."""
    live = probe_lsusb_hardware_version()
    if live:
        cached = read_cached_hardware_version()
        if live != cached:
            write_cached_hardware_version(live)
        return live
    return read_cached_hardware_version()
