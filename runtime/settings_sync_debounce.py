"""Debounce brightness/volume outbound sync publishes."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict

_log = logging.getLogger(__name__)

SETTING_SYNC_DEBOUNCE_SECONDS = 2.0


class SettingsSyncDebouncer:
    """Publish settings patches after a quiet period following rapid local changes."""

    def __init__(
        self,
        publish: Callable[[dict], None],
        *,
        debounce_seconds: float = SETTING_SYNC_DEBOUNCE_SECONDS,
    ) -> None:
        self._publish = publish
        self._debounce_seconds = float(debounce_seconds)
        self._lock = threading.Lock()
        self._pending: Dict[str, int] = {}
        self._timers: Dict[str, threading.Timer] = {}

    def schedule(self, key: str, value: int) -> None:
        setting_key = str(key or "").strip().lower()
        if setting_key not in {"brightness", "volume"}:
            return
        pending_value = int(value)
        with self._lock:
            self._pending[setting_key] = pending_value
            existing = self._timers.pop(setting_key, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(
                self._debounce_seconds,
                self._flush,
                args=(setting_key,),
            )
            timer.daemon = True
            self._timers[setting_key] = timer
            timer.start()

    def _flush(self, key: str) -> None:
        with self._lock:
            value = self._pending.pop(key, None)
            self._timers.pop(key, None)
        if value is None:
            return
        try:
            self._publish({key: value})
        except Exception as e:
            _log.warning("settings sync debounce: publish %s failed: %s", key, e)
