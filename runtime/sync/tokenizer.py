"""Convert raw Supabase row snapshots into semantic sync events."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from runtime.remote_device_config import (
    is_remote_reset_confirmed,
    is_remote_reset_confirmation_source,
    normalize_games_list,
)
from runtime.sync.events import (
    BluetoothChanged,
    DeviceSettingsChanged,
    FirmwareUpdateRequested,
    FullSnapshot,
    GameReadySetChanged,
    GamesSnapshotChanged,
    PagesChanged,
    ResetConfirmed,
    SyncEvent,
    SyncEventMeta,
)
from runtime.sync.timestamps import parse_iso_ts


def _ready_ids_from_games(games_cfg: List[Dict[str, Any]]) -> frozenset[str]:
    out: set[str] = set()
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "").strip()
        if not gid:
            continue
        if str(g.get("status", "")).strip().lower() == "ready":
            out.add(gid)
    return frozenset(out)


def _all_entries_non_ready(games_cfg: List[Dict[str, Any]]) -> bool:
    if not games_cfg:
        return False
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        if str(g.get("status", "")).strip().lower() == "ready":
            return False
    return True


def snapshot_fingerprint(config: Dict[str, Any]) -> str:
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
    return json.dumps(
        {
            "updated_at": config.get("updated_at") or config.get("device_updated_at"),
            "last_update_source": config.get("last_update_source"),
            "games": game_part,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def build_meta(config: Dict[str, Any]) -> SyncEventMeta:
    return SyncEventMeta(
        updated_at=parse_iso_ts(config.get("updated_at") or config.get("device_updated_at")),
        last_update_source=str(config.get("last_update_source") or "").strip().lower(),
        fingerprint=snapshot_fingerprint(config),
    )


def tokenize_snapshot(
    config: Dict[str, Any],
    *,
    is_first_after_connect: bool = False,
    emit_full_snapshot: bool = True,
) -> List[SyncEvent]:
    """Tokenize a normalized remote config snapshot into ordered semantic events."""
    if not isinstance(config, dict):
        return []

    cfg = dict(config)
    meta = build_meta(cfg)
    events: List[SyncEvent] = []

    if emit_full_snapshot:
        events.append(
            FullSnapshot(
                meta=meta,
                config=cfg,
                is_first_after_connect=is_first_after_connect,
            )
        )

    if is_remote_reset_confirmed(cfg) and is_remote_reset_confirmation_source(
        cfg.get("last_update_source")
    ):
        events.append(ResetConfirmed(meta=meta, config=cfg))
        return events

    if "pages" in cfg and isinstance(cfg.get("pages"), list):
        events.append(
            PagesChanged(
                meta=meta,
                pages=list(cfg.get("pages") or []),
                pages_updated_at=parse_iso_ts(cfg.get("pages_updated_at")),
            )
        )

    if "games" in cfg:
        games_cfg = normalize_games_list(cfg)
        events.append(
            GameReadySetChanged(
                meta=meta,
                ready_ids=_ready_ids_from_games(games_cfg),
                games_cfg=games_cfg,
                games_key_present=True,
                games_explicitly_empty=len(games_cfg) == 0,
                all_entries_non_ready=_all_entries_non_ready(games_cfg),
            )
        )
        events.append(GamesSnapshotChanged(meta=meta, games=games_cfg))

    if isinstance(cfg.get("bluetooth"), dict):
        events.append(BluetoothChanged(meta=meta, bluetooth=dict(cfg["bluetooth"])))

    settings_keys = (
        "brightness",
        "Brightness",
        "volume",
        "time_zone",
        "dim_window",
        "device_info",
        "ip_address",
        "ssid",
    )
    settings_subset = {k: cfg[k] for k in settings_keys if k in cfg}
    if settings_subset:
        events.append(DeviceSettingsChanged(meta=meta, config=settings_subset))

    firmware = cfg.get("firmware")
    if isinstance(firmware, dict) and firmware.get("update"):
        events.append(FirmwareUpdateRequested(meta=meta, firmware=dict(firmware)))

    return events
