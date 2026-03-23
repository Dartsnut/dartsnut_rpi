"""
Main entry point: display, device, context, state machine, and main loop.
"""

import base64
import io
import json
import os
import signal
import struct
import subprocess
import threading
import time
import glob
import urllib.request
from datetime import datetime, time as dt_time

# Start the RGB matrix explicitly when the Python service starts.
# We keep `dartsnut_matrix.service` from auto-starting at boot so the
# splash can appear as early as possible, and then `Conflicts=` will
# stop the splash as soon as the matrix is up.
subprocess.run(["systemctl", "start", "dartsnut_matrix.service"], check=False)

# Wait briefly for the matrix to initialize its shared memory.
# `Dartsnut()` will exit if shared memory isn't available.
_matrix_ready_deadline = time.time() + 15
while time.time() < _matrix_ready_deadline:
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", "dartsnut_matrix.service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0 and os.path.exists("/dev/shm/pdishm") and os.path.exists("/dev/shm/pdoshm"):
        break
    time.sleep(0.1)

from PIL import Image
from pydartsnut import Dartsnut

from python_ble.ble_server import start_ble_server
from python_websocket.websocket_server import start_websocket_server
from python_websocket.device_operations import _parse_hhmm, forget_wifi
from python_websocket.git_operations import get_version, perform_update
from python_websocket.udp_broadcast import (
    get_ip_address,
    get_current_ssid,
    normalize_ip,
    normalize_ssid,
)

import assets
from app_context import AppContext
from states import MenuState, WidgetState, GameSelectState, InGameState, SettingsState
from widget_lifecycle import (
    init_pages,
    term_widget_processes,
    check_widget_ready,
)
from game_lifecycle import (
    load_game_list,
    start_game_process,
    term_game_process,
    ensure_game_downloaded,
)
from game_firestore_sync import handle_incoming_game_status
from machine_state_service import (
    init_machine_state_service,
    get_machine_state_service,
)

try:
    from firestore_sync_bridge import (
        start_firestore_sync_if_available,
        notify_device_state_update,
        restart_firestore_sync,
        is_firestore_connected,
        set_firestore_connectivity_callback,
        request_set_game_status,
        request_set_all_games_ready,
    )
except ImportError:
    def start_firestore_sync_if_available(*args, **kwargs):
        return None

    def notify_device_state_update(*args, **kwargs):
        return None

    def restart_firestore_sync(*args, **kwargs):
        return None

    def is_firestore_connected(*args, **kwargs):
        return False

    def set_firestore_connectivity_callback(*args, **kwargs):
        return None

# -----------------------------------------------------------------------------
# Display and device (used by context and dim logic)
# -----------------------------------------------------------------------------
dartsnut = Dartsnut()

# Dim window state (used in main loop)
_currently_in_dim_window = False
_brightness_before_dim = None
_last_dim_check_time = 0
_dim_force_normal_brightness = False
_dim_force_normal_start_time = None

# Smooth brightness transition (1 second to target)
_brightness_transition_start_time = None
_brightness_transition_start_value = None
_brightness_transition_target = None
_brightness_last_set = None

BRIGHTNESS_TRANSITION_DURATION = 1.0

_firmware_update_in_progress = False
_startup_firmware_version = None
# Startup stale window for game commands:
# 1) reset all games to ready in Firestore
# 2) wait until Firestore config confirms all games are ready
# 3) only then apply incoming game commands
_awaiting_games_ready_confirmation = False
_network_state_refresh_event = threading.Event()


def _get_current_brightness_for_transition():
    """Current brightness value (for transition start): last set or from device.json."""
    if _brightness_last_set is not None:
        return _brightness_last_set
    try:
        return int(get_device_info().get("brightness", 50))
    except Exception:
        return 50


def _start_brightness_transition(target):
    """Start or replace a 1-second smooth transition to target brightness (0-100)."""
    global _brightness_transition_start_time, _brightness_transition_start_value, _brightness_transition_target
    now = time.time()
    if (
        _brightness_transition_start_time is not None
        and _brightness_transition_target is not None
    ):
        t = min(
            1.0,
            (now - _brightness_transition_start_time) / BRIGHTNESS_TRANSITION_DURATION,
        )
        start_val = round(
            _brightness_transition_start_value
            + (_brightness_transition_target - _brightness_transition_start_value) * t
        )
    else:
        start_val = _get_current_brightness_for_transition()
    if start_val == target:
        _brightness_transition_start_time = None
        return
    _brightness_transition_start_time = now
    _brightness_transition_start_value = start_val
    _brightness_transition_target = target


def _update_brightness_transition():
    """Run once per frame: advance smooth transition and set hardware."""
    global _brightness_transition_start_time, _brightness_transition_start_value, _brightness_transition_target, _brightness_last_set
    if _brightness_transition_start_time is None:
        return
    elapsed = time.time() - _brightness_transition_start_time
    t = min(1.0, elapsed / BRIGHTNESS_TRANSITION_DURATION)
    current = round(
        _brightness_transition_start_value
        + (_brightness_transition_target - _brightness_transition_start_value) * t
    )
    _set_brightness_hardware(current)
    _brightness_last_set = current
    if t >= 1.0:
        _brightness_transition_start_time = None
        _brightness_transition_start_value = None
        _brightness_transition_target = None


def get_device_info():
    if not hasattr(get_device_info, "_last_mtime"):
        get_device_info._last_mtime = 0
        get_device_info._cached_device_info = None
    file_path = os.path.join(os.getcwd(), "device.json")
    try:
        current_mtime = os.path.getmtime(file_path)
        if (
            current_mtime != get_device_info._last_mtime
            or get_device_info._cached_device_info is None
        ):
            with open(file_path, "r") as file:
                get_device_info._cached_device_info = json.load(file)
            get_device_info._last_mtime = current_mtime
        return get_device_info._cached_device_info
    except Exception:
        return {}


def _set_brightness_hardware(brightness):
    global _brightness_last_set
    _brightness_last_set = brightness
    dartsnut.set_brightness(brightness)


def set_brightness(brightness):
    """
    Public brightness setter used by the rest of the app and websocket layer.
    Delegates persistence to MachineStateService while preserving dim-window
    behavior and smooth transitions.
    """
    global _brightness_before_dim
    service = get_machine_state_service()

    if _currently_in_dim_window:
        # When in dim window, only update stored brightness and Firestore; keep hardware dimmed.
        try:
            if service is not None:
                service.set_brightness(brightness)
            _brightness_before_dim = brightness
            v = int(brightness)
            # Maintain both canonical and legacy-capitalized fields in Firestore
            # so dashboards reading either stay in sync.
            notify_device_state_update({"brightness": v, "Brightness": v})
        except Exception as e:
            print(f"Error updating device brightness while dimmed: {e}")
        return

    # Outside dim window: update hardware smoothly and persist via service.
    _start_brightness_transition(brightness)
    try:
        if service is not None:
            service.set_brightness(brightness)
        v = int(brightness)
        notify_device_state_update({"brightness": v, "Brightness": v})
    except Exception as e:
        print(f"Error updating brightness: {e}")


def set_volume(volume):
    """
    Public volume setter used by the rest of the app and websocket layer.
    Delegates to MachineStateService for hardware + JSON, then notifies Firestore.
    """
    service = get_machine_state_service()
    try:
        if service is not None:
            service.set_volume(volume)
        else:
            # Fallback to previous behavior if service is not initialized.
            if volume == 0:
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "PCM", "mute"],
                    check=True,
                    capture_output=True,
                )
            else:
                mapped_volume = int(50 + (volume / 100) * 50)
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "PCM", "unmute"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "PCM", f"{mapped_volume}%"],
                    check=True,
                    capture_output=True,
                )
        notify_device_state_update({"volume": int(volume)})
    except Exception as e:
        print(f"Error updating volume: {e}")


def set_time_zone(time_zone):
    try:
        subprocess.run(["sudo", "timedatectl", "set-timezone", time_zone], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to set time zone: {e}")
    return None


# -----------------------------------------------------------------------------
# App context and lifecycle callbacks
# -----------------------------------------------------------------------------
ctx = AppContext(
    display=dartsnut,
    assets=assets,
    get_device_info=get_device_info,
    set_brightness=set_brightness,
    set_volume=set_volume,
    set_brightness_hardware=_set_brightness_hardware,
)
ctx.load_game_list = load_game_list
ctx.term_game_process = term_game_process
ctx.start_game_process = start_game_process
ctx.term_widget_processes = term_widget_processes
ctx.reset_device = lambda: forget_wifi()
ctx.set_game_status = request_set_game_status

_app_ctx = ctx


def _are_all_firestore_games_ready(games_cfg) -> bool:
    if not isinstance(games_cfg, list):
        return False
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        status = str(g.get("status", "")).strip().lower()
        if status != "ready":
            return False
    return True


def _apply_firestore_config(config: dict) -> None:
    """
    Apply configuration received from Firestore to the local machine state.

    This now delegates to MachineStateService so that all core state changes go
    through a single abstraction.
    """
    if not isinstance(config, dict):
        return

    global _awaiting_games_ready_confirmation

    service = get_machine_state_service()
    if service is None:
        # Fallback: do nothing if the service is not yet initialized.
        return

    # Pages
    try:
        pages = config.get("pages")
        if isinstance(pages, list):
            service.set_pages(pages)
    except Exception as e:
        print(f"Error applying Firestore pages config: {e}")

    # Brightness / volume / time zone / dim window / device name / games
    try:
        # Brightness can arrive under canonical "brightness" or legacy
        # capitalized "Brightness" from existing Firestore documents.
        if "brightness" in config or "Brightness" in config:
            try:
                key = "brightness" if "brightness" in config else "Brightness"
                brightness_val = int(config.get(key))
                service.set_brightness(brightness_val)
            except Exception:
                pass

        if "volume" in config:
            try:
                volume_val = int(config.get("volume"))
                service.set_volume(volume_val)
            except Exception:
                pass

        if "time_zone" in config:
            # time_zone is still applied via the existing helper
            tz = config.get("time_zone")
            if tz:
                set_time_zone(tz)

        dim_window = config.get("dim_window") or {}
        if isinstance(dim_window, dict):
            dim_cfg = {
                "dim_window_enabled": dim_window.get("dim_window_enabled"),
                "dim_window_start": dim_window.get("dim_window_start"),
                "dim_window_end": dim_window.get("dim_window_end"),
                "dim_level": dim_window.get("dim_level"),
                "dim_restore_seconds": dim_window.get("dim_restore_seconds"),
            }
            service.set_dim_window(dim_cfg)

        device_info = config.get("device_info") or {}
        if isinstance(device_info, dict) and "name" in device_info:
            service.set_device_name(device_info.get("name", ""))

        # Games: handle status commands from Firestore.
        # - "download": set "downloading", fetch game locally, then set "ready"
        # - "playing": ensure game exists (download if needed), then launch
        #              through the same mechanism used by the websocket layer.
        #
        # During startup, wait for confirmation that the ready-reset has landed
        # before acting on incoming game commands.
        games_cfg = config.get("games")
        if isinstance(games_cfg, list):
            if _awaiting_games_ready_confirmation:
                if _are_all_firestore_games_ready(games_cfg):
                    _awaiting_games_ready_confirmation = False
                    print("Firestore game reset confirmed; enabling game command handling")
                else:
                    return

            for g in games_cfg:
                if not isinstance(g, dict):
                    continue
                game_id = g.get("id")
                status = str(g.get("status", "")).strip().lower()
                if not game_id:
                    continue
                game_id = str(game_id)

                def _current_game_id() -> str:
                    if ctx.game and isinstance(ctx.game, dict):
                        return str(ctx.game.get("game_id") or "")
                    return ""

                def _game_exists(gid: str) -> bool:
                    return os.path.isdir(os.path.join(os.getcwd(), "apps", gid))

                def _set_status(gid: str, next_status: str) -> None:
                    try:
                        request_set_game_status(gid, next_status)
                    except Exception as e:
                        print(
                            f"Error updating Firestore game status to {next_status} for {gid}: {e}"
                        )

                def _request_launch(gid: str) -> None:
                    if _current_game_id() != gid:
                        ctx.start_game = True
                        ctx.game_id = gid

                def _terminate_running_game(gid: str) -> None:
                    # Remote "ready" for the same currently-running game means
                    # we should stop the process and leave game mode.
                    if ctx.game and isinstance(ctx.game, dict):
                        running_id = str(ctx.game.get("game_id") or "")
                        if running_id == gid:
                            term_game_process(ctx.game)
                            ctx.game = None
                            ctx.reload_conf = True

                handle_incoming_game_status(
                    game_id,
                    status,
                    current_game_id=_current_game_id(),
                    game_exists=_game_exists,
                    ensure_game_downloaded=ensure_game_downloaded,
                    set_game_status=_set_status,
                    request_launch=_request_launch,
                    terminate_running_game=_terminate_running_game,
                )
                if status == "playing":
                    break
    except Exception as e:
        print(f"Error applying Firestore device config: {e}")

    # One-shot publish of startup firmware version to Firestore once bridge is active.
    global _startup_firmware_version
    try:
        if _startup_firmware_version:
            payload = {
                "firmware": {
                    "version": _startup_firmware_version,
                    "update": False,
                }
            }
            try:
                notify_device_state_update(payload)
            except Exception as e:
                print(f"Error notifying Firestore of startup firmware version: {e}")

            try:
                service.set_firmware_info(_startup_firmware_version, False)
            except Exception as e:
                print(f"Error persisting startup firmware info locally: {e}")

            _startup_firmware_version = None
    except Exception as e:
        print(f"Error handling startup firmware version publish: {e}")

    # Firmware update handling
    global _firmware_update_in_progress
    try:
        firmware_cfg = config.get("firmware") or {}
        if not isinstance(firmware_cfg, dict):
            return
        if not firmware_cfg.get("update"):
            return
        if _firmware_update_in_progress:
            return
        _firmware_update_in_progress = True

        update_result = perform_update()
        if isinstance(update_result, dict) and not update_result.get("error"):
            new_version = "dev"
            try:
                version_result = get_version()
                if (
                    isinstance(version_result, dict)
                    and not version_result.get("error")
                    and version_result.get("version")
                ):
                    new_version = str(version_result.get("version"))
            except Exception as e:
                print(f"Error determining firmware version after update: {e}")

            payload = {
                "firmware": {
                    "version": new_version,
                    "update": False,
                }
            }
            try:
                notify_device_state_update(payload)
            except Exception as e:
                print(f"Error notifying Firestore of firmware update completion: {e}")

            try:
                service.set_firmware_info(new_version, False)
            except Exception as e:
                print(f"Error persisting firmware info locally after update: {e}")
        else:
            print(f"Firmware update requested via Firestore but perform_update failed: {update_result}")
    except Exception as e:
        print(f"Error handling Firestore firmware update config: {e}")
    finally:
        _firmware_update_in_progress = False


def locate_device():
    _app_ctx.locate_device_intv = 60 * 3


def reload_config():
    # WebSocket-driven config reloads should be soft: update pages from ./apps/conf.json
    # without forcing a hard reset back to menu/widgets or killing any running game.
    _app_ctx.reload_pages = True


def get_widgets_framebuffer():
    framebuffers = []
    pages = _app_ctx.pages or []
    for page in pages:
        if page.get("uuid") == "0":
            continue
        img = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
        main_img = img.crop((0, 0, 128, 128))
        main_img_buffer = io.BytesIO()
        main_img.save(main_img_buffer, format="JPEG")
        main_img_base64_str = "data:image/png;base64," + base64.b64encode(
            main_img_buffer.getvalue()
        ).decode("utf-8")
        second_img = img.crop((0, 128, 64, 160))
        second_img_buffer = io.BytesIO()
        second_img.save(second_img_buffer, format="JPEG")
        second_img_base64_str = "data:image/png;base64," + base64.b64encode(
            second_img_buffer.getvalue()
        ).decode("utf-8")
        framebuffers.append(
            {
                "uuid": page["uuid"],
                "main_screen": main_img_base64_str,
                "sec_screen": second_img_base64_str,
            }
        )
    return framebuffers


def start_game_from_websocket(gameid):
    _app_ctx.start_game = True
    _app_ctx.game_id = gameid
    return True


def _ensure_apps_conf_and_load_pages(context: AppContext) -> None:
    """Ensure ./apps/conf.json exists and (re)load pages into context.pages."""
    if not os.path.isdir("./apps"):
        os.makedirs("./apps")
    if not os.path.isfile("./apps/conf.json"):
        default_config = {
            "user": "",
            "date": "",
            "pages": [
                {
                    "uuid": "e7b8c2e2-4f3a-4b7e-9c1a-2d6e8f5a1b3c",
                    "title": "factory_tool",
                    "duration": "60",
                    "combination": "0",
                    "enabled": True,
                    "widgets": [
                        {
                            "id": "factory_tool",
                            "position": [0, 0, 127, 159],
                            "fields": {},
                        }
                    ],
                }
            ],
        }
        with open("./apps/conf.json", "w") as f:
            json.dump(default_config, f)
    with open("./apps/conf.json", "r") as f:
        raw_conf = json.load(f)
    # Defensive normalization: ensure each page has a widgets list so that
    # downstream code (init_pages/start_page_process) never sees None here.
    try:
        pages_conf = raw_conf.get("pages") if isinstance(raw_conf, dict) else None
        if isinstance(pages_conf, list):
            for page in pages_conf:
                if isinstance(page, dict):
                    widgets = page.get("widgets")
                    if not isinstance(widgets, list):
                        page["widgets"] = []
    except Exception:
        pass
    context.pages = init_pages(raw_conf)


# -----------------------------------------------------------------------------
# init_widgets: load config, term existing, init pages, set initial state
# -----------------------------------------------------------------------------
def init_widgets(context: AppContext):
    from states.menu import MenuState
    from states.widget import WidgetState

    if context.current_state and context.current_state.name() == "in_game":
        context.transition_to(MenuState())
    term_widget_processes(context.pages)
    term_game_process(context.game)
    context.game = None
    _ensure_apps_conf_and_load_pages(context)
    context.page_index = 0
    context.last_page_index = -1
    context.next_page_prepared_index = -1
    context.page_freeze = False
    context.locate_device_intv = 0
    context.reload_conf = False
    context.game_index = 0
    context.game_list.clear()
    context.page_tick = time.time()
    device_info = context.get_device_info()
    if device_info.get("model") == "PixelBoard":
        context.transition_to(WidgetState())
    else:
        context.transition_to(MenuState())


def reload_pages_from_conf(context: AppContext) -> None:
    """
    Soft reload of ./apps/conf.json:
    - Terminates existing widget processes and rebuilds context.pages from config
    - Does NOT kill or restart the current game
    - Does NOT force a state transition back to menu/widget
    """
    # Preserve the currently active page by UUID if it still exists after reload.
    preserved_page_uuid = None
    old_pages = context.pages or []
    if old_pages and 0 <= context.page_index < len(old_pages):
        preserved_page_uuid = old_pages[context.page_index].get("uuid")

    term_widget_processes(context.pages)
    _ensure_apps_conf_and_load_pages(context)

    # Default to the first page; if the preserved page still exists, restore it.
    new_index = 0
    if preserved_page_uuid is not None and context.pages:
        for idx, page in enumerate(context.pages):
            if page.get("uuid") == preserved_page_uuid:
                new_index = idx
                break

    context.page_index = new_index
    context.last_page_index = -1
    context.next_page_prepared_index = -1
    context.page_freeze = False
    context.page_tick = time.time()


# -----------------------------------------------------------------------------
# Buttons: GPIO + joystick (skip joystick when in_game so game receives input)
# -----------------------------------------------------------------------------
def get_buttons_pressed(context: AppContext):
    consume_joystick = True
    if context is not None and context.current_state is not None:
        # In in_game without overlay, game gets joystick; with overlay, app handles A/B
        consume_joystick = (
            context.current_state.name() != "in_game"
            or context.current_state.is_showing_exit_game_overlay(context)
        )

    if not hasattr(get_buttons_pressed, "old_buttons"):
        get_buttons_pressed.old_buttons = {
            "btn_a": False,
            "btn_b": False,
            "btn_left": False,
            "btn_up": False,
            "btn_right": False,
            "btn_down": False,
            "btn_home": False,
            "btn_reserved": False,
        }
    button_states = dartsnut.get_buttons()
    button_pressed = {k: False for k in get_buttons_pressed.old_buttons}
    for i, key in enumerate(button_states):
        if button_states[key] != get_buttons_pressed.old_buttons[key]:
            get_buttons_pressed.old_buttons[key] = button_states[key]
            if button_states[key]:
                button_pressed[key] = True
    if not hasattr(get_buttons_pressed, "js_files"):
        get_buttons_pressed.js_files = {}
    for js_path in glob.glob("/dev/input/js*"):
        if js_path not in get_buttons_pressed.js_files:
            try:
                f = open(js_path, "rb")
                os.set_blocking(f.fileno(), False)
                get_buttons_pressed.js_files[js_path] = f
            except Exception:
                pass
    if get_buttons_pressed.js_files and consume_joystick:
        for js_path in list(get_buttons_pressed.js_files.keys()):
            js_file = get_buttons_pressed.js_files[js_path]
            while True:
                try:
                    event_data = js_file.read(8)
                    if event_data is None:
                        break
                    if not event_data:
                        raise OSError("Device disconnected")
                    _time_ms, value, type_, number = struct.unpack("Ihbb", event_data)
                    if type_ & 0x01:
                        if value == 1:
                            if number == 0:
                                button_pressed["btn_a"] = True
                            elif number == 1:
                                button_pressed["btn_b"] = True
                            elif number in (8, 9):
                                button_pressed["btn_home"] = True
                    elif type_ & 0x02:
                        if number == 6:
                            if value < -16000:
                                button_pressed["btn_left"] = True
                            elif value > 16000:
                                button_pressed["btn_right"] = True
                        elif number == 7:
                            if value < -16000:
                                button_pressed["btn_up"] = True
                            elif value > 16000:
                                button_pressed["btn_down"] = True
                except (BlockingIOError, InterruptedError):
                    break
                except Exception:
                    try:
                        js_file.close()
                    except Exception:
                        pass
                    if js_path in get_buttons_pressed.js_files:
                        del get_buttons_pressed.js_files[js_path]
                    break
    return button_pressed


def check_connection_loop():
    while True:
        try:
            wifi_check = subprocess.run(
                ["iwgetid"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            previous_wifi = getattr(_app_ctx, "wifi_connected", False)
            previous_internet = getattr(_app_ctx, "internet_connected", False)

            _app_ctx.wifi_connected = wifi_check.returncode == 0
            if _app_ctx.wifi_connected:
                # Prefer HTTP over ping: many networks block or rate-limit ICMP
                try:
                    urllib.request.urlopen(
                        "https://api.github.com",
                        timeout=5,
                    )
                    _app_ctx.internet_connected = True
                except Exception:
                    _app_ctx.internet_connected = False
            else:
                _app_ctx.internet_connected = False

            if (
                not previous_internet
                and _app_ctx.wifi_connected
                and _app_ctx.internet_connected
            ):
                try:
                    di = get_device_info()
                    restart_firestore_sync(di or {}, reload_config, _apply_firestore_config)
                    request_network_state_refresh()
                except Exception as e:
                    print(f"Error restarting Firestore sync after connectivity established: {e}")
        except Exception as e:
            print(f"Error checking connection: {e}")
            _app_ctx.wifi_connected = False
            _app_ctx.internet_connected = False
        time.sleep(10)


def network_state_firestore_loop():
    """
    Poll current IP/SSID every 30s and only push changed values to Firestore.
    Cache tracks successfully-published values so we resend after bridge restarts.
    """
    poll_interval_seconds = 30
    last_published_ip = None
    last_published_ssid = None

    while True:
        try:
            if _network_state_refresh_event.is_set():
                # Force next publish after startup/reconnect, even if values are unchanged.
                last_published_ip = None
                last_published_ssid = None
                _network_state_refresh_event.clear()

            if is_firestore_connected():
                updates = {}

                normalized_ip = normalize_ip(get_ip_address())
                payload_ip = normalized_ip or ""
                should_publish_ip = payload_ip != last_published_ip
                # Avoid writing a transient empty IP on startup/reconnect before
                # DHCP/network is fully ready. Once we have published any value,
                # normal change-detection behavior resumes.
                if last_published_ip is None and payload_ip == "":
                    should_publish_ip = False
                if should_publish_ip:
                    updates["ip_address"] = payload_ip

                normalized_ssid = normalize_ssid(get_current_ssid())
                payload_ssid = normalized_ssid or ""
                if payload_ssid != last_published_ssid:
                    updates["ssid"] = payload_ssid

                if updates:
                    notify_device_state_update(updates)
                    if "ip_address" in updates:
                        last_published_ip = payload_ip
                    if "ssid" in updates:
                        last_published_ssid = payload_ssid
        except Exception as e:
            print(f"Error in network Firestore poller: {e}")

        _network_state_refresh_event.wait(poll_interval_seconds)


def request_network_state_refresh():
    """
    Trigger an immediate poll cycle and force a republish of IP/SSID on next run.
    """
    _network_state_refresh_event.set()


def _on_firestore_connectivity_changed(connected: bool) -> None:
    if connected:
        request_network_state_refresh()


# -----------------------------------------------------------------------------
# Startup: loading screen, device, threads, init_widgets
# -----------------------------------------------------------------------------
dartsnut.update_frame_buffer(assets.create_loading_image())
device_info = get_device_info() or {}
try:
    version_result = get_version()
    firmware_version = "dev"
    if (
        isinstance(version_result, dict)
        and not version_result.get("error")
        and version_result.get("version")
    ):
        firmware_version = str(version_result.get("version"))
    device_info["firmware_version"] = firmware_version
    _startup_firmware_version = firmware_version
except Exception as e:
    print(f"Error determining firmware version for Firestore initial state: {e}")
if "firmware_update" not in device_info:
    device_info["firmware_update"] = False
set_volume(int(device_info.get("volume", "50")))

ble_thread = threading.Thread(
    target=start_ble_server, args=(locate_device,), daemon=True
)
ble_thread.start()


def trigger_dim_check():
    _app_ctx.trigger_dim_check = True


websocket_thread = threading.Thread(
    target=start_websocket_server,
    args=(
        set_brightness,
        locate_device,
        reload_config,
        set_time_zone,
        get_widgets_framebuffer,
        start_game_from_websocket,
        set_volume,
        trigger_dim_check,
    ),
    daemon=True,
)
websocket_thread.start()
connection_thread = threading.Thread(target=check_connection_loop, daemon=True)
connection_thread.start()
network_firestore_thread = threading.Thread(
    target=network_state_firestore_loop, daemon=True
)
network_firestore_thread.start()
set_firestore_connectivity_callback(_on_firestore_connectivity_changed)
request_network_state_refresh()

try:
    start_firestore_sync_if_available(device_info or {}, reload_config, _apply_firestore_config)
except Exception as e:
    print(f"Failed to start Firestore sync: {e}")
else:
    # On service start, proactively reset all local games to \"ready\" status in
    # Firestore so any stale \"playing\" flags from previous runs are cleared.
    try:
        request_set_all_games_ready()
        _awaiting_games_ready_confirmation = True
    except Exception as e:
        print(f"Error resetting Firestore game statuses to ready on startup: {e}")

ctx.reload_conf = False
ctx.start_game = False
ctx.menu_select_index = 0
ctx.setting_select_index = 3

# Initialize machine state service and widgets after context is ready.
init_machine_state_service(
    ctx,
    set_brightness_hardware=_set_brightness_hardware,
    get_device_info=get_device_info,
    reload_pages_from_conf=reload_pages_from_conf,
)
init_widgets(ctx)


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
while dartsnut.running:
    try:
        time.sleep(1 / 30)
        assets.get_current_loading_frame()

        _update_brightness_transition()

        if ctx.trigger_dim_check:
            ctx.trigger_dim_check = False
            _last_dim_check_time = 0

        # Dim window: 60s check
        if (time.time() - _last_dim_check_time) >= 60 or _last_dim_check_time == 0:
            _last_dim_check_time = time.time()
            di = get_device_info()
            enabled = str(di.get("dim_window_enabled", "false")).lower() == "true"
            start_s = (di.get("dim_window_start") or "").strip()
            end_s = (di.get("dim_window_end") or "").strip()
            dim_lvl = int(di.get("dim_level", 10))
            if not enabled or not start_s or not end_s:
                if _currently_in_dim_window:
                    restore = (
                        _brightness_before_dim
                        if _brightness_before_dim is not None
                        else int(di.get("brightness", 50))
                    )
                    _start_brightness_transition(restore)
                    _currently_in_dim_window = False
                    _dim_force_normal_brightness = False
                    _dim_force_normal_start_time = None
            else:
                start_hm = _parse_hhmm(start_s)
                end_hm = _parse_hhmm(end_s)
                if start_hm is None or end_hm is None:
                    if _currently_in_dim_window:
                        restore = (
                            _brightness_before_dim
                            if _brightness_before_dim is not None
                            else int(di.get("brightness", 50))
                        )
                        _start_brightness_transition(restore)
                        _currently_in_dim_window = False
                        _dim_force_normal_brightness = False
                        _dim_force_normal_start_time = None
                else:
                    now = datetime.now().time()
                    start_t = dt_time(start_hm[0], start_hm[1])
                    end_t = dt_time(end_hm[0], end_hm[1])
                    in_window = (start_t <= end_t and start_t <= now <= end_t) or (
                        start_t > end_t and (now >= start_t or now < end_t)
                    )
                    if in_window:
                        if (
                            ctx.current_state.name() != "in_game"
                            and ctx.current_state.name() != "game_select"
                            and not ctx.current_state.is_showing_exit_game_overlay(ctx)
                            and not _dim_force_normal_brightness
                        ):
                            if not _currently_in_dim_window:
                                _brightness_before_dim = int(di.get("brightness", 50))
                            _start_brightness_transition(dim_lvl)
                            _currently_in_dim_window = True
                    else:
                        if _currently_in_dim_window:
                            restore = (
                                _brightness_before_dim
                                if _brightness_before_dim is not None
                                else int(di.get("brightness", 50))
                            )
                            _start_brightness_transition(restore)
                            _currently_in_dim_window = False
                            _dim_force_normal_brightness = False
                            _dim_force_normal_start_time = None

        ctx.state_str = ctx.current_state.name()

        if ctx.locate_device_intv:
            dartsnut.update_frame_buffer(assets.identify_image)
            ctx.locate_device_intv -= 1
        elif ctx.reload_conf:
            ctx.reload_conf = False
            init_widgets(ctx)
        elif getattr(ctx, "reload_pages", False):
            ctx.reload_pages = False
            reload_pages_from_conf(ctx)
        elif ctx.start_game:
            ctx.start_game = False
            term_game_process(ctx.game)
            ctx.game = start_game_process(ctx.game_id)
            if ctx.game is not None:
                term_widget_processes(ctx.pages)
                ctx.transition_to(InGameState())
                # Reflect the runtime status back to Firestore so that the
                # launched game is marked as \"playing\".
                try:
                    request_set_game_status(ctx.game_id, "playing")
                except Exception as e:
                    print(f"Error updating Firestore game status to playing: {e}")
        else:
            ctx.current_state.update(ctx)

        buttons = get_buttons_pressed(ctx)
        ctx.current_button_state = dict(get_buttons_pressed.old_buttons)

        # Dim window: btn_a force normal, btn_b remove force (menu/widget/settings only)
        if (
            ctx.current_state.name() != "in_game"
            and ctx.current_state.name() != "game_select"
            and not ctx.current_state.is_showing_exit_game_overlay(ctx)
            and _currently_in_dim_window
        ):
            di = get_device_info()
            dim_lvl = int(di.get("dim_level", 10))
            # Remove force after dim_restore_seconds
            if (
                _dim_force_normal_brightness
                and _dim_force_normal_start_time is not None
            ):
                secs = max(5, min(300, int(di.get("dim_restore_seconds", 30))))
                if time.time() - _dim_force_normal_start_time >= secs:
                    _dim_force_normal_brightness = False
                    _dim_force_normal_start_time = None
                    _start_brightness_transition(dim_lvl)
            if _dim_force_normal_brightness and buttons.get("btn_b"):
                # Let B go to state when it has a meaning: menu exit overlay (end game), game_select (back to menu), or settings reset overlay (dismiss)
                btn_b_handled_by_state = (
                    ctx.current_state.is_showing_exit_game_overlay(ctx)
                    or ctx.current_state.name() == "game_select"
                    or ctx.current_state.consumes_btn_b_for_overlay(ctx)
                )
                if not btn_b_handled_by_state:
                    _dim_force_normal_brightness = False
                    _dim_force_normal_start_time = None
                    _start_brightness_transition(dim_lvl)
                    buttons["btn_b"] = False
            elif not _dim_force_normal_brightness and buttons.get("btn_a"):
                _dim_force_normal_brightness = True
                _dim_force_normal_start_time = time.time()
                restore = (
                    _brightness_before_dim
                    if _brightness_before_dim is not None
                    else int(di.get("brightness", 50))
                )
                _start_brightness_transition(restore)
                buttons["btn_a"] = False

        ctx.current_state.handle_input(ctx, buttons)

        # Render widgets: update all page framebuffers from shared memory.
        # Be defensive about page/widget structure so that transient Firestore
        # or config issues don't crash the main loop.
        if ctx.pages is not None and len(ctx.pages) > 0:
            for page in ctx.pages:
                if not isinstance(page, dict):
                    continue

                framebuffer = page.get("framebuffer")
                if framebuffer is None:
                    # Skip pages that have not been fully initialized yet.
                    continue

                widgets = page.get("widgets")
                if not isinstance(widgets, list):
                    # If widgets are missing or malformed, skip this page but keep running.
                    continue

                page_img = Image.frombytes(
                    "RGB", (128, 160), bytes(framebuffer)
                )
                current_loading_frame_big = assets.get_current_loading_frame_big()
                current_loading_frame = assets.get_current_loading_frame()
                if current_loading_frame_big.mode != "RGB":
                    current_loading_frame_big = current_loading_frame_big.convert("RGB")
                if current_loading_frame.mode != "RGB":
                    current_loading_frame = current_loading_frame.convert("RGB")

                for widget in widgets:
                    if not isinstance(widget, dict):
                        continue

                    widget_data = widget.get("widget") or widget
                    if not isinstance(widget_data, dict):
                        continue

                    widget_id = widget_data.get("id", "unknown")
                    position = widget_data.get("position")
                    if (
                        not isinstance(position, (list, tuple))
                        or len(position) != 4
                    ):
                        # Invalid position data; skip this widget.
                        continue
                    x0, y0, x1, y1 = position

                    widget_width = x1 - x0 + 1
                    widget_height = y1 - y0 + 1
                    widget_frame = None

                    shm = widget.get("shm")
                    if shm is not None:
                        width = widget_width
                        height = widget_height
                        try:
                            buf = getattr(shm, "buf", None)
                            if buf is not None:
                                widget_frame = Image.frombytes(
                                    "RGB",
                                    (width, height),
                                    bytes(buf[1 : 1 + width * height * 3]),
                                )
                                page_img.paste(widget_frame, (x0, y0))
                                if buf[0] == 0:
                                    buf[0] = 1
                        except Exception as e:
                            print(f"Error reading widget frame for {widget_id}: {e}")

                    widget_ready = False
                    if widget_frame is not None:
                        was_not_launched = not widget.get("launched", False)
                        widget_ready = check_widget_ready(widget_frame)
                        if widget_ready:
                            widget["launched"] = True
                            if widget_height == 160 and was_not_launched:
                                small_widget_area = widget_frame.crop(
                                    (0, 128, widget_width, 160)
                                )
                                area_bytes = small_widget_area.tobytes()
                                widget["has_small_widget"] = any(
                                    byte != 0 for byte in area_bytes
                                )

                    if not widget_ready:
                        if widget_height == 160:
                            page_img.paste(current_loading_frame_big, (x0, y0 + 32))
                            has_small_widget = widget.get("has_small_widget", None)
                            if has_small_widget is not False:
                                page_img.paste(current_loading_frame, (x0, y0 + 128))
                        elif widget_height == 128:
                            page_img.paste(current_loading_frame_big, (x0, y0 + 32))
                        elif widget_height == 32:
                            page_img.paste(current_loading_frame, (x0, y0))

                page["framebuffer"] = bytearray(page_img.tobytes())
    except Exception as e:
        print(f"Error in main loop: {e}")
