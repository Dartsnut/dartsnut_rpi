"""Unified UTC timestamp parsing for sync ordering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def normalize_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_iso_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return normalize_utc_naive(datetime.fromtimestamp(float(value), tz=timezone.utc))
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return normalize_utc_naive(datetime.fromisoformat(s))
        if isinstance(value, datetime):
            return normalize_utc_naive(value)
    except Exception:
        return None
    return None


def is_newer_than(candidate: Optional[datetime], baseline: Optional[datetime]) -> bool:
    if candidate is None:
        return baseline is None
    if baseline is None:
        return True
    return candidate > baseline
