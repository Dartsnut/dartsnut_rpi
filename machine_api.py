"""
Local machine I/O facade for the presentation layer and composition root.

Keeps `states/*` from importing python_websocket or firestore modules directly.
Game downloads and app files remain in `game_lifecycle` / `widget_lifecycle` for now;
call those modules from here when adding new presentation-layer entry points.
"""

from __future__ import annotations


def stop_game_tracking() -> None:
    from python_websocket import user_data_operations as udo

    udo.stop_game_tracking()


def reset_user_data_file() -> None:
    from python_websocket import user_data_operations as udo

    udo.reset_user_data_file()
