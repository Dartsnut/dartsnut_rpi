"""Stable content fingerprints for inbound remote row dedupe and ordering."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from runtime.remote_device_config import normalize_games_list, resolve_snapshot_updated_at


def _bluetooth_fingerprint(bluetooth: Any) -> Dict[str, Any]:
    if not isinstance(bluetooth, dict):
        return {}
    controllers: List[tuple[str, str]] = []
    for row in bluetooth.get("controllers") or []:
        if not isinstance(row, dict):
            continue
        mac = str(row.get("mac") or row.get("address") or "").strip().upper()
        status = str(row.get("status") or "").strip().lower()
        if mac:
            controllers.append((mac, status))
    return {
        "is_scan": bool(bluetooth.get("is_scan")),
        "controllers": sorted(controllers),
    }


def snapshot_content_fingerprint(config: Dict[str, Any]) -> str:
    """Fingerprint remote row content used for dedupe and stale-row acceptance."""
    games = normalize_games_list(config)
    game_part = sorted(
        (
            str(g.get("id") or ""),
            str(g.get("status") or "").strip().lower(),
            str(g.get("version") or ""),
        )
        for g in games
        if isinstance(g, dict)
    )
    pages_uuids: List[str] = []
    pages = config.get("pages")
    if isinstance(pages, list):
        pages_uuids = sorted(
            str(p.get("uuid") or "")
            for p in pages
            if isinstance(p, dict) and p.get("uuid") is not None
        )
    brightness = config.get("brightness")
    if brightness is None and "Brightness" in config:
        brightness = config.get("Brightness")
    effective_updated_at = resolve_snapshot_updated_at(config)
    payload = {
        "updated_at": (
            effective_updated_at.isoformat() if effective_updated_at is not None else None
        ),
        "device_updated_at": config.get("device_updated_at"),
        "last_update_source": config.get("last_update_source"),
        "brightness": brightness,
        "volume": config.get("volume"),
        "time_zone": config.get("time_zone"),
        "pages_updated_at": config.get("pages_updated_at"),
        "pages_uuids": pages_uuids,
        "games": game_part,
        "bluetooth": _bluetooth_fingerprint(config.get("bluetooth")),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
