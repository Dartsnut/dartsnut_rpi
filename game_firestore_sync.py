"""Helpers for reconciling incoming Firestore game status commands."""

from typing import Callable, Optional


def handle_incoming_game_status(
    game_id: str,
    status: str,
    *,
    current_game_id: Optional[str],
    game_exists: Callable[[str], bool],
    ensure_game_downloaded: Callable[[str], bool],
    set_game_status: Callable[[str, str], None],
    request_launch: Callable[[str], None],
    terminate_running_game: Callable[[str], None],
) -> None:
    """Apply one incoming game status command from Firestore."""
    if not game_id:
        return

    normalized = str(status or "").strip().lower()
    if normalized == "download":
        set_game_status(game_id, "downloading")
        if ensure_game_downloaded(game_id):
            set_game_status(game_id, "ready")
        return

    if normalized == "playing":
        if not game_exists(game_id):
            set_game_status(game_id, "downloading")
            if not ensure_game_downloaded(game_id):
                return
        request_launch(game_id)
        return

    if normalized == "ready" and current_game_id == game_id:
        terminate_running_game(game_id)
        set_game_status(game_id, "ready")
