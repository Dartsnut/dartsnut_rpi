"""Process-local inbound-only game secrets from remote sync."""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_pico8_key = ""


def remember_game_secrets_from_games(games: Any) -> None:
    """Remember inbound-only secrets from remote games entries."""
    if not isinstance(games, list):
        return
    next_key = None
    for item in games:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() != "pico8":
            continue
        if "key" in item:
            next_key = str(item.get("key") or "").strip()
    if next_key is None:
        return
    with _lock:
        global _pico8_key
        _pico8_key = next_key


def get_pico8_key() -> str:
    with _lock:
        return _pico8_key


def clear_game_secrets() -> None:
    with _lock:
        global _pico8_key
        _pico8_key = ""
