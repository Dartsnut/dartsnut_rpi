"""Brightness curves, calibration persistence, and compatibility mapping."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

BRIGHTNESS_LEVEL_VALUES = [10, 13, 16, 22, 32, 45, 61, 69, 80, 95]
BRIGHTNESS_LEVEL_VALUES_444F = [10, 13, 18, 22, 31, 42, 45, 58, 63, 80]
BRIGHTNESS_LEVEL_COUNT = 10
CALIBRATION_FILE_NAME = "brightness_calibration.json"
CALIBRATION_FORMAT_VERSION = 1


def calibration_path(path: str | None = None) -> str:
    return path or os.path.join(os.getcwd(), CALIBRATION_FILE_NAME)


def default_values_for_device(device_info: dict[str, Any] | None) -> list[int]:
    version = str((device_info or {}).get("hardware_version", "")).strip().lower()
    return list(BRIGHTNESS_LEVEL_VALUES_444F if version == "444f" else BRIGHTNESS_LEVEL_VALUES)


def _valid_values(values: Any) -> bool:
    if not isinstance(values, list) or len(values) != BRIGHTNESS_LEVEL_COUNT:
        return False
    try:
        parsed = [int(value) for value in values]
    except (TypeError, ValueError):
        return False
    return (
        all(1 <= value <= 100 for value in parsed)
        and parsed == sorted(parsed)
        and len(set(parsed)) == BRIGHTNESS_LEVEL_COUNT
    )


def load_calibration(
    device_info: dict[str, Any] | None = None, path: str | None = None
) -> list[int] | None:
    """Load valid calibration curve; return None for missing/invalid/mismatched data."""
    try:
        with open(calibration_path(path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("format_version") != CALIBRATION_FORMAT_VERSION:
            return None
        stored_version = str(payload.get("hardware_version", "")).strip().lower()
        current_version = str((device_info or {}).get("hardware_version", "")).strip().lower()
        if stored_version and current_version and stored_version != current_version:
            return None
        values = payload.get("values")
        if not _valid_values(values):
            return None
        return [int(value) for value in values]
    except (OSError, ValueError, TypeError):
        return None


def load_calibration_state(
    device_info: dict[str, Any] | None = None, path: str | None = None
) -> dict[str, Any] | None:
    """Return validated calibration payload, including optional current level."""
    try:
        with open(calibration_path(path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("format_version") != CALIBRATION_FORMAT_VERSION:
            return None
        stored_version = str(payload.get("hardware_version", "")).strip().lower()
        current_version = str((device_info or {}).get("hardware_version", "")).strip().lower()
        if stored_version and current_version and stored_version != current_version:
            return None
        if not _valid_values(values):
            return None
        result = dict(payload)
        result["values"] = [int(value) for value in values]
        try:
            result["current_level"] = max(0, min(9, int(result.get("current_level", 5))))
        except (TypeError, ValueError):
            result["current_level"] = 5
        return result
    except (OSError, ValueError, TypeError):
        return None


def save_calibration(
    values: Sequence[int],
    device_info: dict[str, Any] | None = None,
    path: str | None = None,
    current_level: int = 5,
) -> str:
    """Atomically replace calibration file, never touching device.json."""
    values_list = [int(value) for value in values]
    if not _valid_values(values_list):
        raise ValueError("calibration must contain 10 sorted values in range 1..100")
    destination = calibration_path(path)
    directory = os.path.dirname(destination) or "."
    os.makedirs(directory, exist_ok=True)
    payload = {
        "format_version": CALIBRATION_FORMAT_VERSION,
        "hardware_version": str((device_info or {}).get("hardware_version", "")).strip().lower(),
        "values": values_list,
        "current_level": max(0, min(9, int(current_level))),
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
    }
    fd, temporary = tempfile.mkstemp(prefix=".brightness_calibration.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return destination


def save_brightness_level(
    level: int, device_info: dict[str, Any] | None = None, path: str | None = None
) -> str | None:
    """Update current index in calibration file when a calibration exists."""
    payload = load_calibration_state(device_info, path)
    if payload is None:
        return None
    return save_calibration(
        payload["values"],
        device_info,
        path,
        current_level=max(0, min(9, int(level))),
    )


def values_for_device(device_info: dict[str, Any] | None) -> list[int]:
    return load_calibration(device_info) or default_values_for_device(device_info)


def nearest_index(raw_value: Any, values: Iterable[int]) -> int:
    curve = [int(value) for value in values]
    if not curve:
        return 0
    try:
        raw = int(raw_value)
    except (TypeError, ValueError):
        raw = curve[len(curve) // 2]
    return min(range(len(curve)), key=lambda index: (abs(curve[index] - raw), index))


def default_raw_for_index(index: Any, device_info: dict[str, Any] | None) -> int:
    curve = default_values_for_device(device_info)
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 5
    return curve[max(0, min(len(curve) - 1, index))]


def calibrated_raw_for_index(index: Any, device_info: dict[str, Any] | None) -> int:
    curve = values_for_device(device_info)
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 5
    return curve[max(0, min(len(curve) - 1, index))]


def select_evenly(values: Iterable[int], count: int = BRIGHTNESS_LEVEL_COUNT) -> list[int]:
    """Select evenly spaced sorted values, preserving endpoints."""
    curve = sorted({int(value) for value in values})
    if len(curve) < count:
        raise ValueError(f"need at least {count} passing brightness values")
    if count <= 1:
        return [curve[0]]
    indexes = [round(index * (len(curve) - 1) / (count - 1)) for index in range(count)]
    return [curve[index] for index in indexes]
