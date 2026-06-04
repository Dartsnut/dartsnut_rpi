"""Timestamped reducer: accept or reject semantic events using local sync cache."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import FrozenSet, List, Optional, Tuple

from runtime.sync.events import (
    FullSnapshot,
    GameReadySetChanged,
    SyncEvent,
    SyncEventMeta,
    event_meta,
)
from runtime.sync.game_ready import resolve_authoritative_ready_ids
from runtime.sync.timestamps import is_newer_than

_log = logging.getLogger(__name__)

_BRIDGE_SOURCES = frozenset({"supabase_bridge", "supabase_bridge_init"})
_DUPLICATE_SNAPSHOT_WINDOW_SECONDS = 2.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class SyncCache:
    last_row_updated_at: Optional[datetime] = None
    last_snapshot_fingerprint: str = ""
    last_snapshot_fingerprint_at: Optional[datetime] = None
    game_ready_ids: Optional[FrozenSet[str]] = None
    has_seen_remote_row: bool = False


@dataclass
class ReducedGameReady:
    ready_ids: FrozenSet[str]
    should_reload_menu: bool


class SyncReducer:
    def __init__(self) -> None:
        self.cache = SyncCache()

    def reduce(self, events: List[SyncEvent]) -> Tuple[List[SyncEvent], Optional[ReducedGameReady]]:
        if not events:
            return [], None

        if not self._accept_row_meta(event_meta(events[0])):
            return [], None

        accepted: List[SyncEvent] = []
        game_ready: Optional[ReducedGameReady] = None

        for event in events:
            if isinstance(event, GameReadySetChanged):
                reduced = self._reduce_game_ready(event)
                if reduced is not None:
                    game_ready = reduced
                continue

            accepted.append(event)
            if isinstance(event, FullSnapshot):
                self.cache.has_seen_remote_row = True

        return accepted, game_ready

    def _accept_row_meta(self, meta: SyncEventMeta) -> bool:
        fingerprint_changed = meta.fingerprint != self.cache.last_snapshot_fingerprint
        timestamp_newer = is_newer_than(meta.updated_at, self.cache.last_row_updated_at)

        if meta.last_update_source in _BRIDGE_SOURCES:
            if (
                not fingerprint_changed
                and self.cache.last_snapshot_fingerprint_at is not None
                and meta.updated_at is not None
                and (meta.updated_at - self.cache.last_snapshot_fingerprint_at).total_seconds()
                < _DUPLICATE_SNAPSHOT_WINDOW_SECONDS
            ):
                return False

        if self.cache.has_seen_remote_row and not timestamp_newer:
            if meta.last_update_source in _BRIDGE_SOURCES:
                _log.debug(
                    "sync reducer: reject stale bridge row updated_at=%s source=%s",
                    meta.updated_at,
                    meta.last_update_source,
                )
                return False
            if not fingerprint_changed:
                _log.debug(
                    "sync reducer: reject stale remote row updated_at=%s source=%s",
                    meta.updated_at,
                    meta.last_update_source,
                )
                return False

        if meta.updated_at is not None and timestamp_newer:
            self.cache.last_row_updated_at = meta.updated_at
        self.cache.last_snapshot_fingerprint = meta.fingerprint
        self.cache.last_snapshot_fingerprint_at = meta.updated_at or _utc_now()
        return True

    def _reduce_game_ready(self, event: GameReadySetChanged) -> Optional[ReducedGameReady]:
        previous = self.cache.game_ready_ids
        authoritative = resolve_authoritative_ready_ids(
            previous,
            event.games_cfg,
            games_key_present=event.games_key_present,
        )
        if authoritative is None:
            return None

        should_reload = previous != authoritative
        self.cache.game_ready_ids = authoritative
        return ReducedGameReady(ready_ids=authoritative, should_reload_menu=should_reload)
