"""Helpers for reconciling incoming remote game status commands."""

from typing import Callable, Optional


def are_remote_playing_games_cleared(games_cfg) -> bool:
    """True when there are no remote game entries currently marked playing."""
    if not isinstance(games_cfg, list):
        return False
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        status = str(g.get("status", "")).strip().lower()
        if status == "playing":
            return False
    return True


def handle_incoming_game_status(
    game_id: str,
    status: str,
    *,
    expected_version: str = "",
    current_game_id: Optional[str],
    game_exists: Callable[[str], bool],
    ensure_game_downloaded: Callable[[str, str], bool],
    set_game_status: Callable[[str, str], None],
    request_launch: Callable[[str], None],
    terminate_running_game: Callable[[str], None],
) -> None:
    """Apply one incoming game status command from remote sync."""
    if not game_id:
        return

    normalized = str(status or "").strip().lower()
    if normalized == "downloading":
        set_game_status(game_id, "downloading")
        if ensure_game_downloaded(game_id, expected_version):
            set_game_status(game_id, "ready")
        return

    if normalized == "playing":
        if not game_exists(game_id):
            set_game_status(game_id, "downloading")
            if not ensure_game_downloaded(game_id, expected_version):
                return
        request_launch(game_id)
        return

    if normalized == "ready" and current_game_id == game_id:
        terminate_running_game(game_id)
        set_game_status(game_id, "ready")
