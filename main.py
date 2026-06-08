"""
Main entry point: display, device, context, state machine, and main loop.
"""

import base64
import io
import json
import logging
import os
import struct
import subprocess
import threading
import time
import glob
import urllib.request

# Optional legacy startup path: Python can still start matrix service explicitly.
# Default is disabled so matrix ownership/timing lives in systemd boot sequence.
_start_matrix_on_python_start = os.getenv("DARTSNUT_START_MATRIX_ON_PYTHON_START", "0").strip().lower()
if _start_matrix_on_python_start in ("1", "true", "yes", "on"):
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
from python_websocket.remote_bluetooth_sync import RemoteBluetoothScanController
from python_websocket.file_operations import cancel_game_download

import assets
from domain.app_context import AppContext
from states import MenuState, WidgetState, GameSelectState, InGameState, SettingsState
from widget_lifecycle import (
    init_pages,
    term_widget_processes,
    check_widget_ready,
    try_soft_apply_remote_supabase_pages,
)
from game_lifecycle import (
    load_menu_game_list,
    refresh_menu_game_list_if_requested,
    start_game_process,
    term_game_process,
    ensure_game_downloaded,
    local_game_version_matches,
)
import runtime.machine_api as machine_api
from machine_state_service import (
    init_machine_state_service,
    get_machine_state_service,
)
from runtime.remote_device_config import (
    RemoteConfigRuntimeState,
    RemoteDeviceConfigApplier,
    RemoteDeviceConfigDependencies,
)
from runtime.remote_sync_port import (
    create_default_remote_sync,
    get_remote_sync,
    set_remote_sync,
)
from runtime.reset_workflow import request_confirm_and_forget_wifi
from runtime.display_loop import DimWindowRuntime, run_main_loop
from runtime.bootstrap import start_background_subsystems
from runtime.logging_config import configure_logging
from runtime.websocket_service_registry import build_default_websocket_registry
from runtime.pixeldarts_hardware import resolve_pixeldarts_hardware_version

_effective_log_level = configure_logging()
_log = logging.getLogger(__name__)
_log.info("Logging initialized (effective level: %s)", _effective_log_level)

# -----------------------------------------------------------------------------
# Display and device (used by context and dim logic)
# -----------------------------------------------------------------------------
dartsnut = Dartsnut()

# Dim window state (shared with set_brightness and display loop)
dim_rt = DimWindowRuntime()

# Smooth brightness transition (1 second to target)
_brightness_transition_start_time = None
_brightness_transition_start_value = None
_brightness_transition_target = None
_brightness_last_set = None

BRIGHTNESS_TRANSITION_DURATION = 1.0

# Remote config/game startup coordination (see RemoteConfigRuntimeState).
_remote_config_runtime = RemoteConfigRuntimeState()
_network_state_refresh_event = threading.Event()
_reset_in_progress = False
_reset_lock = threading.Lock()
_reset_remote_confirm_event = threading.Event()
_RESET_CONFIRM_TIMEOUT_SECONDS = 10.0

set_remote_sync(create_default_remote_sync())

_remote_bluetooth_scan_controller = RemoteBluetoothScanController(
    scan_builder=machine_api.build_remote_bluetooth_list,
    timestamp_factory=machine_api.current_utc_iso_timestamp,
    publish_update=lambda p: get_remote_sync().publish_partial_state(p),
    connect_device=machine_api.connect_device_for_remote,
    connected_controllers_provider=machine_api.list_connected_paired_devices,
)
_websocket_service_registry = build_default_websocket_registry()


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
        base = get_device_info._cached_device_info
        if not isinstance(base, dict):
            return {}
        hardware_version = resolve_pixeldarts_hardware_version()
        if hardware_version:
            merged = dict(base)
            merged["hardware_version"] = hardware_version
            return merged
        return base
    except Exception:
        return {}


def _set_brightness_hardware(brightness):
    global _brightness_last_set
    _brightness_last_set = brightness
    dartsnut.set_brightness(brightness)


def _current_device_int(key):
    try:
        return int((get_device_info() or {}).get(key))
    except Exception:
        return None


def set_brightness(brightness):
    """
    Public brightness setter used by the rest of the app and websocket layer.
    Delegates persistence to MachineStateService while preserving dim-window
    behavior and smooth transitions.
    """
    service = get_machine_state_service()

    if dim_rt.currently_in_dim_window:
        # When in dim window, only update stored brightness and remote sync; keep hardware dimmed.
        try:
            v = int(brightness)
            should_publish = _current_device_int("brightness") != v
            if service is not None:
                service.set_brightness(brightness)
            dim_rt.brightness_before_dim = brightness
            if should_publish:
                get_remote_sync().publish_partial_state({"brightness": v})
                from runtime.remote_device_config import note_local_setting_change

                note_local_setting_change(_remote_config_runtime, "brightness", v)
        except Exception as e:
            _log.warning("Error updating device brightness while dimmed: %s", e)
        return

    # Outside dim window: apply immediately and persist via service.
    try:
        v = int(brightness)
        should_publish = _current_device_int("brightness") != v
        if service is not None:
            service.set_brightness(brightness)
        else:
            _set_brightness_hardware(v)
        if should_publish:
            get_remote_sync().publish_partial_state({"brightness": v})
            from runtime.remote_device_config import note_local_setting_change

            note_local_setting_change(_remote_config_runtime, "brightness", v)
    except Exception as e:
        _log.warning("Error updating brightness: %s", e)


def _set_volume_local_only(volume):
    v = int(volume)
    service = get_machine_state_service()
    if service is not None:
        service.set_volume(v)
        return

    # Fallback to previous behavior if service is not initialized.
    if v == 0:
        subprocess.run(
            ["amixer", "-c", "0", "sset", "PCM", "mute"],
            check=True,
            capture_output=True,
        )
    else:
        mapped_volume = int(50 + (v / 100) * 50)
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


def set_volume(volume):
    """
    Public volume setter used by the rest of the app and websocket layer.
    Delegates to MachineStateService for hardware + JSON, then notifies remote sync.
    """
    try:
        v = int(volume)
        should_publish = _current_device_int("volume") != v
        _set_volume_local_only(v)
        if should_publish:
            get_remote_sync().publish_partial_state({"volume": v})
            from runtime.remote_device_config import note_local_setting_change

            note_local_setting_change(_remote_config_runtime, "volume", v)
    except Exception as e:
        _log.warning("Error updating volume: %s", e)


def set_time_zone(time_zone):
    tz = str(time_zone or "").strip()
    if not tz:
        return None
    try:
        current_tz_result = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            check=True,
            capture_output=True,
            text=True,
        )
        current_tz = (current_tz_result.stdout or "").strip()
        if current_tz == tz:
            return None
        subprocess.run(["sudo", "timedatectl", "set-timezone", tz], check=True)
    except subprocess.CalledProcessError as e:
        _log.error("Failed to set time zone: %s", e)
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
ctx.load_game_list = lambda: load_menu_game_list(ctx)
ctx.term_game_process = term_game_process
ctx.start_game_process = start_game_process
ctx.term_widget_processes = term_widget_processes
def _set_game_status_from_local_ui(game_id: str, status: str) -> None:
    from runtime.remote_device_config import note_local_game_transition

    note_local_game_transition(_remote_config_runtime, game_id, status)
    get_remote_sync().request_set_game_status(game_id, status)


ctx.set_game_status = _set_game_status_from_local_ui

_app_ctx = ctx


def _is_reset_in_progress() -> bool:
    with _reset_lock:
        return _reset_in_progress


def _set_reset_in_progress(value: bool) -> None:
    global _reset_in_progress
    with _reset_lock:
        _reset_in_progress = bool(value)


def _run_device_reset_sequence() -> None:
    service = get_machine_state_service()
    if service is None:
        _log.warning("Reset aborted: MachineStateService is not initialized")
        return
    if _is_reset_in_progress():
        return
    _log.info("device reset: sequence started")
    _set_reset_in_progress(True)
    _network_state_refresh_event.clear()
    try:
        request_confirm_and_forget_wifi(
            request_device_reset_state=get_remote_sync().request_device_reset_state,
            confirm_event=_reset_remote_confirm_event,
            confirm_timeout_seconds=_RESET_CONFIRM_TIMEOUT_SECONDS,
            forget_wifi=machine_api.forget_wifi,
        )
        try:
            term_widget_processes(ctx.pages)
        except Exception as e:
            _log.error("Error terminating widget processes during reset: %s", e)
        try:
            if ctx.game:
                term_game_process(ctx.game)
                ctx.game = None
        except Exception as e:
            _log.error("Error terminating game process during reset: %s", e)
        try:
            machine_api.reset_user_data_file()
        except Exception as e:
            _log.error("Error resetting user data during device reset: %s", e)
        service.clear_apps_directory_contents()
        service.reset_device_to_factory_fields()
    except Exception as e:
        _log.error("Error during device reset sequence: %s", e)
    finally:
        _set_reset_in_progress(False)


def _start_device_reset() -> None:
    if _is_reset_in_progress():
        return
    threading.Thread(target=_run_device_reset_sequence, daemon=True).start()


ctx.reset_device = _start_device_reset


_remote_config_applier = RemoteDeviceConfigApplier(
    RemoteDeviceConfigDependencies(
        app_ctx=ctx,
        get_machine_state_service=get_machine_state_service,
        bluetooth_scan_controller=_remote_bluetooth_scan_controller,
        publish_partial_state=lambda p: get_remote_sync().publish_partial_state(p),
        request_set_game_status=lambda gid, s: get_remote_sync().request_set_game_status(
            gid, s
        ),
        disconnect_and_unpair_device=(
            _websocket_service_registry.bluetooth_ops.disconnect_and_unpair_device
        ),
        request_config_refresh=lambda: get_remote_sync().restart_sync(
            get_device_info() or {},
            reload_config,
            _apply_remote_config,
            _on_sync_game_ready,
        ),
        set_time_zone=set_time_zone,
        term_game_process=term_game_process,
        ensure_game_downloaded=ensure_game_downloaded,
        cancel_game_download=cancel_game_download,
        local_game_version_matches=local_game_version_matches,
        perform_update=machine_api.perform_update,
        get_version=machine_api.get_version,
        is_reset_in_progress=_is_reset_in_progress,
        on_reset_confirmed=_reset_remote_confirm_event.set,
        try_soft_apply_remote_supabase_pages=try_soft_apply_remote_supabase_pages,
    ),
    _remote_config_runtime,
)


def _apply_remote_config(config: dict) -> None:
    """Apply remote configuration; implementation in remote_device_config."""
    _remote_config_applier.apply(config)


def _on_sync_game_ready(reduced) -> None:
    from supabase_sync_bridge import get_sync_engine

    engine = get_sync_engine()
    if engine is None:
        return
    engine.apply_game_ready_to_ctx(_app_ctx, reduced)


def locate_device():
    _app_ctx.locate_device_intv = 60 * 3


def reload_config():
    # Supabase inbound callbacks invoke this for any row change, including non-page
    # fields such as volume/brightness. Page reload intent is set explicitly by the
    # remote config applier via ctx.reload_pages when pages truly changed.
    refresh_menu_game_list_if_requested(_app_ctx)


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


# Linux joystick button index -> app button name (Bluetooth gamepads).
_JS_BUTTON_TO_APP = {
    0: "btn_a",
    1: "btn_b",
    8: "btn_home",
    9: "btn_home",
    10: "btn_home",
}


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
            except OSError:
                pass
    if get_buttons_pressed.js_files:
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
                    event_kind = type_ & 0x7F  # JS_EVENT_* (ignore JS_EVENT_INIT)
                    if event_kind == 0x01:
                        app_btn = _JS_BUTTON_TO_APP.get(number)
                        if value != 0 and app_btn and (
                            consume_joystick or app_btn == "btn_home"
                        ):
                            button_pressed[app_btn] = True
                    elif consume_joystick and event_kind == 0x02:
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

            if previous_wifi != _app_ctx.wifi_connected:
                _log.info("network: wifi_associated=%s", _app_ctx.wifi_connected)
            if previous_internet != _app_ctx.internet_connected:
                _log.info("network: internet_reachable=%s", _app_ctx.internet_connected)

            if (
                not previous_internet
                and _app_ctx.wifi_connected
                and _app_ctx.internet_connected
            ):
                try:
                    di = get_device_info()
                    get_remote_sync().restart_sync(
                        di or {},
                        reload_config,
                        _apply_remote_config,
                        _on_sync_game_ready,
                    )
                    request_network_state_refresh()
                except Exception as e:
                    _log.warning(
                        "Error restarting remote sync after connectivity established: %s", e
                    )
        except Exception as e:
            _log.warning("Error checking connection: %s", e)
            _app_ctx.wifi_connected = False
            _app_ctx.internet_connected = False
        time.sleep(10)


def network_state_remote_loop():
    """
    Poll current IP/SSID every 30s and only push changed values to remote sync.
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

            if not _is_reset_in_progress() and get_remote_sync().is_connected():
                updates = {}

                normalized_ip = machine_api.normalize_ip(machine_api.get_ip_address())
                payload_ip = normalized_ip or ""
                should_publish_ip = payload_ip != last_published_ip
                # Avoid writing a transient empty IP on startup/reconnect before
                # DHCP/network is fully ready. Once we have published any value,
                # normal change-detection behavior resumes.
                if last_published_ip is None and payload_ip == "":
                    should_publish_ip = False
                if should_publish_ip:
                    updates["ip_address"] = payload_ip

                normalized_ssid = machine_api.normalize_ssid(machine_api.get_current_ssid())
                payload_ssid = normalized_ssid or ""
                if payload_ssid != last_published_ssid:
                    updates["ssid"] = payload_ssid

                if updates:
                    get_remote_sync().publish_partial_state(updates)
                    if "ip_address" in updates:
                        last_published_ip = payload_ip
                    if "ssid" in updates:
                        last_published_ssid = payload_ssid
        except Exception as e:
            _log.warning("Error in network sync poller: %s", e)

        _network_state_refresh_event.wait(poll_interval_seconds)


def request_network_state_refresh():
    """
    Trigger an immediate poll cycle and force a republish of IP/SSID on next run.
    """
    _network_state_refresh_event.set()


def _on_remote_connectivity_changed(connected: bool) -> None:
    if connected and not _is_reset_in_progress():
        request_network_state_refresh()


def trigger_dim_check():
    _app_ctx.trigger_dim_check = True


device_info = get_device_info() or {}
start_background_subsystems(
    dartsnut=dartsnut,
    device_info=device_info,
    get_version=machine_api.get_version,
    set_volume=set_volume,
    start_ble_server=start_ble_server,
    locate_device=locate_device,
    start_websocket_server=start_websocket_server,
    set_brightness=set_brightness,
    reload_config=reload_config,
    set_time_zone=set_time_zone,
    get_widgets_framebuffer=get_widgets_framebuffer,
    start_game_from_websocket=start_game_from_websocket,
    trigger_dim_check=trigger_dim_check,
    check_connection_loop=check_connection_loop,
    network_state_remote_loop=network_state_remote_loop,
    apply_remote_config=_apply_remote_config,
    on_sync_game_ready=_on_sync_game_ready,
    on_remote_connectivity_changed=_on_remote_connectivity_changed,
    request_network_state_refresh=request_network_state_refresh,
    remote_config_runtime=_remote_config_runtime,
    websocket_service_registry=_websocket_service_registry,
    set_startup_volume=_set_volume_local_only,
)

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


run_main_loop(
    dim_rt=dim_rt,
    dartsnut=dartsnut,
    ctx=ctx,
    assets=assets,
    get_device_info=get_device_info,
    parse_hhmm=machine_api.parse_hhmm,
    update_brightness_transition=_update_brightness_transition,
    start_brightness_transition=_start_brightness_transition,
    init_widgets=init_widgets,
    reload_pages_from_conf=reload_pages_from_conf,
    term_game_process=term_game_process,
    start_game_process=start_game_process,
    term_widget_processes=term_widget_processes,
    get_buttons_pressed=get_buttons_pressed,
    check_widget_ready=check_widget_ready,
    in_game_state_cls=InGameState,
    get_remote_sync=get_remote_sync,
)
