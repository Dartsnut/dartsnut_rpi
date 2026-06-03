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
    ) -> None:
        self._send_fn = send_fn
        self._backoff = backoff_seconds
        self._lock = threading.Lock()
        self._pending: Dict[str, OutboxEntry] = {}
        self._order: List[str] = []
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
        entry = OutboxEntry(ref=ref, patch=dict(patch), full=full, source=source)
        with self._lock:
            self._pending[ref] = entry
            self._order.append(ref)
        self._flush_ready()
        return ref

    def on_ack(self, ref: str) -> None:
        with self._lock:
            self._pending.pop(ref, None)
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
            if not ok:
                with self._lock:
                    e = self._pending.get(ref)
                    if e is not None:
                        e.attempts += 1
                        idx = min(e.attempts, len(self._backoff) - 1)
                        e.next_retry_at = time.monotonic() + self._backoff[idx]
