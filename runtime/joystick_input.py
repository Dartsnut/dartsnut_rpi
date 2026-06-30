from __future__ import annotations

from typing import Any


def _current_state_name(context: Any) -> str:
    state = getattr(context, "current_state", None)
    if state is None:
        return ""
    try:
        return str(state.name())
    except Exception:
        return ""


def _is_showing_exit_game_overlay(context: Any) -> bool:
    state = getattr(context, "current_state", None)
    if state is None:
        return False
    try:
        return bool(state.is_showing_exit_game_overlay(context))
    except Exception:
        return False


def _current_game_id(context: Any) -> str:
    game = getattr(context, "game", None)
    if not isinstance(game, dict):
        return ""
    return str(game.get("game_id") or "")


def should_consume_joystick(context: Any) -> bool:
    if context is not None and getattr(context, "current_state", None) is not None:
        return _current_state_name(context) != "in_game" or _is_showing_exit_game_overlay(
            context
        )
    return True


def should_consume_joystick_button(context: Any, app_btn: str | None) -> bool:
    if should_consume_joystick(context):
        return True
    if app_btn != "btn_home":
        return False
    return _current_game_id(context) != "pico8"
