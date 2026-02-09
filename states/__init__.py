"""State classes for menu, widget, game, and settings."""
from states.base import BaseState
from states.menu import MenuState
from states.widget import WidgetState
from states.game import GameSelectState, InGameState
from states.settings import SettingsState

__all__ = [
    "BaseState",
    "MenuState",
    "WidgetState",
    "GameSelectState",
    "InGameState",
    "SettingsState",
]
