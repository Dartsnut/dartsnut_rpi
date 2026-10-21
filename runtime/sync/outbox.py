"""Outbound patch queue with retry backoff and transport ack tracking."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from runtime.sync.outcomes import OUTCOME_ACKED, OUTCOME_ERROR, OUTCOME_PENDING

_log = logging.getLogger(__name__)

_DEFAULT_BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
_SCALAR_COALESCE_KEYS = frozenset(
    {"volume", "brightness", "ssid", "ip_address", "rssi", "device_updated_at"}
)

# Socket message kind for a queued entry. Every entry is an ordinary state patch
# except a reset, which the bridge routes to its own RPC. A reset is a command
# rather than a state value, so it is never coalesced with anything.
KIND_PATCH = "patch"
KIND_RESET = "reset_device"

# How many refs keep their transport outcome available. An outcome exists so a
# caller can watch the one call it made; nothing enumerates them, so the history
# is bounded rather than growing for the process lifetime.
_OUTCOME_HISTORY_LIMIT = 256


@dataclass
class OutboxEntry:
    ref: str
    patch: Dict[str, Any]
    full: bool
    source: Optional[str]
    attempts: int = 0
    next_retry_at: float = 0.0
    kind: str = KIND_PATCH


class SyncOutbox:
    def __init__(
        self,
        send_fn: Callable[[str, Dict[str, Any], bool, Optional[str]], bool],
        *,
        backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF_SECONDS,
        ack_timeout_seconds: float = 30.0,
        reset_send_fn: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._send_fn = send_fn
        self._backoff = backoff_seconds
        self._ack_timeout_seconds = max(0.1, float(ack_timeout_seconds))
        self._reset_send_fn = reset_send_fn
        self._lock = threading.Lock()
        self._pending: Dict[str, OutboxEntry] = {}
        self._order: List[str] = []
        self._last_acked_scalars: Dict[str, Any] = {}
        self._outcomes: Dict[str, str] = {}
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
        kind: str = KIND_PATCH,
    ) -> str:
        ref = str(uuid.uuid4())
        next_patch = dict(patch)
        is_patch = kind == KIND_PATCH
        with self._lock:
            if is_patch and not full and self._is_scalar_only(next_patch):
                if all(
                    self._last_acked_scalars.get(key) == value
                    for key, value in next_patch.items()
                ):
                    return ""
                next_patch = self._coalesce_pending_scalars(next_patch, source)
            elif (
                is_patch
                and not full
                and self._is_single_game_status_patch(next_patch)
            ):
                next_patch = self._coalesce_pending_game_status(next_patch, source)
            entry = OutboxEntry(
                ref=ref, patch=next_patch, full=full, source=source, kind=kind
            )
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
            self._record_outcome(ref, OUTCOME_ACKED)

    def on_error(self, ref: str, message: str = "") -> None:
        with self._lock:
            # The report is recorded whether or not the entry is still queued: a
            # failed attempt keeps the entry for retry, but the caller watching
            # this ref needs to know the attempt failed.
            self._record_outcome(ref, OUTCOME_ERROR)
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

    def ref_status(self, ref: str) -> Optional[str]:
        """Transport outcome of a ref, or None when the ref is unknown.

        OUTCOME_PENDING until the transport reports anything, then OUTCOME_ACKED
        or OUTCOME_ERROR from the most recent report. OUTCOME_ERROR is not final —
        the entry stays queued for retry, so a later ack supersedes it — which is
        what lets a caller keep waiting on a call that failed once.
        """
        if not ref:
            return None
        with self._lock:
            reported = self._outcomes.get(ref)
            if reported is not None:
                return reported
            if ref in self._pending:
                return OUTCOME_PENDING
            return None

    def _record_outcome(self, ref: str, status: str) -> None:
        """Remember a ref's latest transport report, keeping the history bounded.

        Caller holds `self._lock`.
        """
        if not ref:
            return
        self._outcomes.pop(ref, None)
        self._outcomes[ref] = status
        while len(self._outcomes) > _OUTCOME_HISTORY_LIMIT:
            oldest = next(iter(self._outcomes))
            self._outcomes.pop(oldest, None)

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
                or entry.kind != KIND_PATCH
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
                or entry.kind != KIND_PATCH
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

    def _send_entry(self, entry: OutboxEntry) -> bool:
        """Write one entry to the transport.

        A reset has its own sender: its socket frame carries a different kind and
        no state payload, so it must never travel through the patch sender. With
        no reset sender wired the entry is reported as unsent (a failed write,
        which the backoff retries) rather than silently going out as a patch.
        """
        if entry.kind == KIND_RESET:
            if self._reset_send_fn is None:
                _log.warning(
                    "sync outbox: no reset sender wired; ref=%s will retry", entry.ref
                )
                return False
            return self._reset_send_fn(entry.ref)
        return self._send_fn(entry.ref, entry.patch, entry.full, entry.source)

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
            ok = self._send_entry(entry)
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
