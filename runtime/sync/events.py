"""Semantic sync events tokenized from raw Supabase remote row snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional


@dataclass(frozen=True)
class SyncEventMeta:
    updated_at: Optional[datetime]
    last_update_source: str
    fingerprint: str


@dataclass(frozen=True)
class FullSnapshot:
    meta: SyncEventMeta
    config: Dict[str, Any]
    is_first_after_connect: bool = False


@dataclass(frozen=True)
class PagesChanged:
    meta: SyncEventMeta
    pages: List[Any]
    pages_updated_at: Optional[datetime]


@dataclass(frozen=True)
class GameReadySetChanged:
    meta: SyncEventMeta
    ready_ids: FrozenSet[str]
    games_cfg: List[Dict[str, Any]]
    games_key_present: bool
    games_explicitly_empty: bool
    all_entries_non_ready: bool


@dataclass(frozen=True)
class GamesSnapshotChanged:
    meta: SyncEventMeta
    games: List[Dict[str, Any]]


@dataclass(frozen=True)
class BluetoothChanged:
    meta: SyncEventMeta
    bluetooth: Dict[str, Any]


@dataclass(frozen=True)
class DeviceSettingsChanged:
    meta: SyncEventMeta
    config: Dict[str, Any]


@dataclass(frozen=True)
class FirmwareUpdateRequested:
    meta: SyncEventMeta
    firmware: Dict[str, Any]


@dataclass(frozen=True)
class ResetConfirmed:
    meta: SyncEventMeta
    config: Dict[str, Any]


SyncEvent = (
    FullSnapshot
    | PagesChanged
    | GameReadySetChanged
    | GamesSnapshotChanged
    | BluetoothChanged
    | DeviceSettingsChanged
    | FirmwareUpdateRequested
    | ResetConfirmed
)


def event_meta(event: SyncEvent) -> SyncEventMeta:
    return event.meta  # type: ignore[attr-defined]
