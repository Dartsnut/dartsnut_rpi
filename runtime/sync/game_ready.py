"""Authoritative game-ready set computation (resilient under bad networks)."""

from __future__ import annotations

from typing import FrozenSet, List, Optional

from runtime.remote_device_config import normalize_games_list


def ready_ids_from_games_cfg(games_cfg: list) -> frozenset[str]:
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


def all_entries_non_ready(games_cfg: list) -> bool:
    if not games_cfg:
        return False
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        if str(g.get("status", "")).strip().lower() == "ready":
            return False
    return True


def resolve_authoritative_ready_ids(
    previous: Optional[FrozenSet[str]],
    games_cfg: list,
    *,
    games_key_present: bool,
) -> Optional[frozenset[str]]:
    """
    Return the ready-id set to apply, or None to hold the previous set.

    None means do not change ``remote_menu_ready_game_ids``.
    """
    if not games_key_present:
        return None

    new_ids = ready_ids_from_games_cfg(games_cfg)
    if previous is None:
        return new_ids

    if len(new_ids) == 0 and len(previous) > 0:
        if len(games_cfg) == 0:
            return new_ids
        if all_entries_non_ready(games_cfg):
            return None

    return new_ids
