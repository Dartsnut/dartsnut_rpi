"""Outbound patch queue with retry backoff and transport ack tracking."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

_DEFAULT_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
_SCALAR_COALESCE_KEYS = frozenset(
    {"volume", "brightness", "ssid", "ip_address", "device_updated_at"}
)


@dataclass
class OutboxEntry:
    ref: str
    patch: Dict[str, Any]
    full: bool
    source: Optional[str]
    attempts: int = 0
    next_retry_at: float = 0.0


class SyncOutbox:
    def __init__(
        self,
        send_fn: Callable[[str, Dict[str, Any], bool, Optional[str]], bool],
        *,
        backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF_SECONDS,
        ack_timeout_seconds: float = 30.0,
    ) -> None:
        self._send_fn = send_fn
        self._backoff = backoff_seconds
        self._ack_timeout_seconds = max(0.1, float(ack_timeout_seconds))
        self._lock = threading.Lock()
        self._pending: Dict[str, OutboxEntry] = {}
        self._order: List[str] = []
        self._last_acked_scalars: Dict[str, Any] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="sync-outbox")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def enqueue(
        self,
        patch: Dict[str, Any],
        *,
        full: bool = False,
        source: Optional[str] = None,
    ) -> str:
        ref = str(uuid.uuid4())
        next_patch = dict(patch)
        with self._lock:
            if not full and self._is_scalar_only(next_patch):
                if all(
                    self._last_acked_scalars.get(key) == value
                    for key, value in next_patch.items()
                ):
                    return ""
                next_patch = self._coalesce_pending_scalars(next_patch, source)
            elif not full and self._is_single_game_status_patch(next_patch):
                next_patch = self._coalesce_pending_game_status(next_patch, source)
            entry = OutboxEntry(ref=ref, patch=next_patch, full=full, source=source)
            self._pending[ref] = entry
            self._order.append(ref)
        self._flush_ready()
        return ref

    def on_ack(self, ref: str) -> None:
        with self._lock:
            entry = self._pending.pop(ref, None)
            if entry is not None and not entry.full and self._is_scalar_only(entry.patch):
                for key, value in entry.patch.items():
                    self._last_acked_scalars[key] = value
            if ref in self._order:
                self._order.remove(ref)

    def on_error(self, ref: str, message: str = "") -> None:
        with self._lock:
            entry = self._pending.get(ref)
            if entry is None:
                return
            entry.attempts += 1
            idx = min(entry.attempts - 1, len(self._backoff) - 1)
            entry.next_retry_at = time.monotonic() + self._backoff[idx]
            _log.warning(
                "sync outbox: retry ref=%s attempt=%s err=%s",
                ref,
                entry.attempts,
                message or "?",
            )

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _is_scalar_only(self, patch: Dict[str, Any]) -> bool:
        return bool(patch) and set(patch).issubset(_SCALAR_COALESCE_KEYS)

    def _is_single_game_status_patch(self, patch: Dict[str, Any]) -> bool:
        if set(patch) != {"games"}:
            return False
        games = patch.get("games")
        if not isinstance(games, list) or len(games) != 1:
            return False
        game = games[0]
        return (
            isinstance(game, dict)
            and bool(str(game.get("id") or "").strip())
            and bool(str(game.get("status") or "").strip())
        )

    def _single_game_patch_id(self, patch: Dict[str, Any]) -> str:
        games = patch.get("games")
        if not isinstance(games, list) or not games or not isinstance(games[0], dict):
            return ""
        return str(games[0].get("id") or "").strip()

    def _coalesce_pending_scalars(
        self, patch: Dict[str, Any], source: Optional[str]
    ) -> Dict[str, Any]:
        merged = dict(patch)
        removed: list[str] = []
        for ref in list(self._order):
            entry = self._pending.get(ref)
            if (
                entry is None
                or entry.full
                or entry.source != source
                or not self._is_scalar_only(entry.patch)
            ):
                continue
            merged = {**entry.patch, **merged}
            self._pending.pop(ref, None)
            removed.append(ref)
        for ref in removed:
            self._order.remove(ref)
        return merged

    def _coalesce_pending_game_status(
        self, patch: Dict[str, Any], source: Optional[str]
    ) -> Dict[str, Any]:
        game_id = self._single_game_patch_id(patch)
        if not game_id:
            return patch
        removed: list[str] = []
        for ref in list(self._order):
            entry = self._pending.get(ref)
            if (
                entry is None
                or entry.full
                or entry.source != source
                or not self._is_single_game_status_patch(entry.patch)
                or self._single_game_patch_id(entry.patch) != game_id
            ):
                continue
            self._pending.pop(ref, None)
            removed.append(ref)
        for ref in removed:
            self._order.remove(ref)
        return patch

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self._flush_ready()

    def _flush_ready(self) -> None:
        now = time.monotonic()
        with self._lock:
            refs = list(self._order)
        for ref in refs:
            with self._lock:
                entry = self._pending.get(ref)
            if entry is None:
                continue
            if entry.next_retry_at > now:
                continue
            ok = self._send_fn(entry.ref, entry.patch, entry.full, entry.source)
            with self._lock:
                e = self._pending.get(ref)
                if e is None:
                    continue
                if ok:
                    e.next_retry_at = time.monotonic() + self._ack_timeout_seconds
                else:
                    e.attempts += 1
                    idx = min(e.attempts, len(self._backoff) - 1)
                    e.next_retry_at = time.monotonic() + self._backoff[idx]
