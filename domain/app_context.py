"""Application context: display, assets, device, and all mutable app state."""
# Optional type hint for current_state (avoids circular import at runtime)
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional, FrozenSet

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from states.base import BaseState


class AppContext:
    """Holds display, assets, device accessors, and app state. Single source of truth for mutable state."""

    def __init__(
        self,
        display: Any,
        assets: Any,
        get_device_info: Callable[[], dict],
        set_brightness: Callable[[int], None],
        set_volume: Callable[[int], None],
        set_brightness_hardware: Optional[Callable[[int], None]] = None,
        bluetooth_controller: Any = None,
        wifi_controller: Any = None,
    ):
        self.display = display
        self.assets = assets
        self.get_device_info = get_device_info
        self.set_brightness = set_brightness
        self.set_volume = set_volume
        self._set_brightness_hardware = set_brightness_hardware or set_brightness
        self.bluetooth_controller = bluetooth_controller
        self.wifi_controller = wifi_controller

        # App state (replaces globals)
        self.pages = None
        self.page_index = 0
        self.page_freeze = False
        self.game = None
        self.game_list = []
        self.game_index = 0
        self.menu_select_index = 0
        self.setting_select_index = 3
        self.page_tick = 0.0
        self.last_page_index = -1
        self.next_page_prepared_index = -1
        self.start_game = False
        self.game_id = None
        self.wifi_connected = False
        self.internet_connected = False
        self.locate_device_intv = 0
        # Flags for config reload behavior
        # reload_conf: hard reload via init_widgets (used for failures / explicit resets)
        # reload_pages: soft reload of ./apps/conf.json pages without forcing a state reset
        # reload_game_menu: set when inbound remote config includes a games list; consumed by reload_config
        self.reload_conf = False
        self.reload_pages = False
        self.reload_game_menu = False
        self.game_preview_index = 0
        # Remote-sync games with status "ready"; None until first games list applied
        self.remote_menu_ready_game_ids: Optional[FrozenSet[str]] = None

        # Current state (object, not string)
        self.current_state: "BaseState" = None
        # When using string state in main loop: next state name set by state.handle_input
        self.pending_state: Optional[str] = None
        # Current state name (set by main each frame) for lifecycle callbacks that run in threads
        self.state_str: str = "menu"
        # Set True when exiting in_game so main loop runs dim check on next frame
        self.trigger_dim_check: bool = False

        # Lifecycle callbacks (set by main after creating context)
        self.load_game_list: Optional[Callable[[], list]] = None
        self.term_game_process: Optional[Callable[[Any], Any]] = None
        self.start_game_process: Optional[Callable[[str], Any]] = None
        self.term_widget_processes: Optional[Callable[[Any], None]] = None
        self.reset_device: Optional[Callable[[], None]] = None
        self.set_game_status: Optional[Callable[[str, str], None]] = None

    def transition_to(self, new_state: "BaseState") -> None:
        """Switch to a new state."""
        prev = (
            self.current_state.name()
            if self.current_state is not None
            else None
        )
        nxt = new_state.name()
        self.current_state = new_state
        self.state_str = nxt
        if prev != nxt:
            _log.info("ui state: %s -> %s", prev or "(none)", nxt)

    @property
    def set_brightness_hardware(self):
        """Set brightness on hardware only (for dim/restore)."""
        return self._set_brightness_hardware
