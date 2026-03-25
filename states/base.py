"""Abstract base state: update (render) and handle_input (buttons)."""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from domain.app_context import AppContext


class BaseState(ABC):
    """One frame of logic/rendering and button handling. Transitions via ctx.transition_to(new_state)."""

    @abstractmethod
    def update(self, ctx: "AppContext") -> None:
        """One frame: update state and render to display (e.g. ctx.display.update_frame_buffer(...))."""
        pass

    @abstractmethod
    def handle_input(self, ctx: "AppContext", buttons: Dict[str, bool]) -> None:
        """Process button map; perform transitions with ctx.transition_to(NewState())."""
        pass

    def name(self) -> str:
        """State name for joystick consumption (e.g. 'in_game' means don't consume joystick)."""
        return ""

    def is_showing_exit_game_overlay(self, ctx: "AppContext") -> bool:
        """True when this state is showing the 'B: End the game' overlay (B should end game, not dim override)."""
        return False

    def consumes_btn_b_for_overlay(self, ctx: "AppContext") -> bool:
        """True when this state is showing an overlay that handles btn_b (e.g. reset confirm); dim logic should not consume btn_b."""
        return False
