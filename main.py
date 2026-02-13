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

from PIL import Image
from pydartsnut import Dartsnut

from python_ble.ble_server import start_ble_server
from python_websocket.websocket_server import start_websocket_server
from python_websocket.device_operations import _parse_hhmm, forget_wifi

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
)

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
    if _brightness_transition_start_time is not None and _brightness_transition_target is not None:
        t = min(1.0, (now - _brightness_transition_start_time) / BRIGHTNESS_TRANSITION_DURATION)
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
        if current_mtime != get_device_info._last_mtime or get_device_info._cached_device_info is None:
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
    global _brightness_before_dim
    if _currently_in_dim_window:
        try:
            device_info = get_device_info()
            device_info["brightness"] = str(brightness)
            with open("./device.json", "w") as file:
                json.dump(device_info, file)
            _brightness_before_dim = brightness
        except Exception as e:
            print(f"Error updating device info: {e}")
        return
    _set_brightness_hardware(brightness)
    try:
        device_info = get_device_info()
        device_info["brightness"] = str(brightness)
        with open("./device.json", "w") as file:
            json.dump(device_info, file)
    except Exception as e:
        print(f"Error updating device info: {e}")


def set_volume(volume):
    try:
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
        device_info = get_device_info()
        device_info["volume"] = str(volume)
        with open("./device.json", "w") as file:
            json.dump(device_info, file)
    except subprocess.CalledProcessError as e:
        print(f"Failed to set volume: {e.stderr.decode().strip()}")
    except Exception as e:
        print(f"Error updating device info: {e}")


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

_app_ctx = ctx


def locate_device():
    _app_ctx.locate_device_intv = 60 * 3


def reload_config():
    _app_ctx.reload_conf = True


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
        main_img_base64_str = "data:image/png;base64," + base64.b64encode(main_img_buffer.getvalue()).decode("utf-8")
        second_img = img.crop((0, 128, 64, 160))
        second_img_buffer = io.BytesIO()
        second_img.save(second_img_buffer, format="JPEG")
        second_img_base64_str = "data:image/png;base64," + base64.b64encode(second_img_buffer.getvalue()).decode("utf-8")
        framebuffers.append({
            "uuid": page["uuid"],
            "main_screen": main_img_base64_str,
            "sec_screen": second_img_base64_str,
        })
    return framebuffers


def start_game_from_websocket(gameid):
    _app_ctx.start_game = True
    _app_ctx.game_id = gameid
    return True


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
                        {"id": "factory_tool", "position": [0, 0, 127, 159], "fields": {}}
                    ],
                }
            ],
        }
        with open("./apps/conf.json", "w") as f:
            json.dump(default_config, f)
    with open("./apps/conf.json", "r") as f:
        context.pages = init_pages(json.load(f))
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


# -----------------------------------------------------------------------------
# Buttons: GPIO + joystick (skip joystick when in_game so game receives input)
# -----------------------------------------------------------------------------
def get_buttons_pressed(context: AppContext):
    consume_joystick = True
    if context is not None and context.current_state is not None:
        consume_joystick = context.current_state.name() != "in_game"

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
        except Exception as e:
            print(f"Error checking connection: {e}")
            _app_ctx.wifi_connected = False
            _app_ctx.internet_connected = False
        time.sleep(10)


# -----------------------------------------------------------------------------
# Startup: loading screen, device, threads, init_widgets
# -----------------------------------------------------------------------------
dartsnut.update_frame_buffer(assets.create_loading_image())
device_info = get_device_info()
set_volume(int(device_info.get("volume", "50")))

ble_thread = threading.Thread(target=start_ble_server, args=(locate_device,), daemon=True)
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

ctx.reload_conf = False
ctx.start_game = False
ctx.menu_select_index = 0
ctx.setting_select_index = 3
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
                    restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
                    _start_brightness_transition(restore)
                    _currently_in_dim_window = False
                    _dim_force_normal_brightness = False
                    _dim_force_normal_start_time = None
            else:
                start_hm = _parse_hhmm(start_s)
                end_hm = _parse_hhmm(end_s)
                if start_hm is None or end_hm is None:
                    if _currently_in_dim_window:
                        restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
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
                        if (ctx.current_state.name() != "in_game" 
                            and ctx.current_state.name() != "game_select"
                            and not ctx.current_state.is_showing_exit_game_overlay(ctx)
                            and not _dim_force_normal_brightness):
                            if not _currently_in_dim_window:
                                _brightness_before_dim = int(di.get("brightness", 50))
                            _start_brightness_transition(dim_lvl)
                            _currently_in_dim_window = True
                    else:
                        if _currently_in_dim_window:
                            restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
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
        elif ctx.start_game:
            ctx.start_game = False
            term_game_process(ctx.game)
            ctx.game = start_game_process(ctx.game_id)
            if ctx.game is not None:
                term_widget_processes(ctx.pages)
                ctx.transition_to(InGameState())
        else:
            ctx.current_state.update(ctx)

        buttons = get_buttons_pressed(ctx)

        # Dim window: btn_a force normal, btn_b remove force (menu/widget/settings only)
        if (ctx.current_state.name() != "in_game" 
            and ctx.current_state.name() != "game_select"
            and not ctx.current_state.is_showing_exit_game_overlay(ctx)
            and _currently_in_dim_window):
            di = get_device_info()
            dim_lvl = int(di.get("dim_level", 10))
            # Remove force after dim_restore_seconds
            if _dim_force_normal_brightness and _dim_force_normal_start_time is not None:
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
                restore = _brightness_before_dim if _brightness_before_dim is not None else int(di.get("brightness", 50))
                _start_brightness_transition(restore)
                buttons["btn_a"] = False

        ctx.current_state.handle_input(ctx, buttons)

        # Render widgets: update all page framebuffers from shared memory
        if ctx.pages is not None and len(ctx.pages) > 0:
            for page in ctx.pages:
                page_img = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
                current_loading_frame_big = assets.get_current_loading_frame_big()
                current_loading_frame = assets.get_current_loading_frame()
                if current_loading_frame_big.mode != "RGB":
                    current_loading_frame_big = current_loading_frame_big.convert("RGB")
                if current_loading_frame.mode != "RGB":
                    current_loading_frame = current_loading_frame.convert("RGB")
                for widget in page["widgets"]:
                    widget_data = widget.get("widget")
                    if widget_data is None:
                        continue
                    widget_id = widget_data.get("id", "unknown")
                    shm = widget.get("shm")
                    x0, y0, x1, y1 = widget_data["position"]
                    widget_width = x1 - x0 + 1
                    widget_height = y1 - y0 + 1
                    widget_frame = None
                    if shm is not None:
                        width = x1 - x0 + 1
                        height = y1 - y0 + 1
                        try:
                            widget_frame = Image.frombytes(
                                "RGB",
                                (width, height),
                                bytes(shm.buf[1 : 1 + width * height * 3]),
                            )
                            page_img.paste(widget_frame, (x0, y0))
                            if shm.buf[0] == 0:
                                shm.buf[0] = 1
                        except Exception as e:
                            print(f"Error reading widget frame for {widget_id}: {e}")
                    widget_ready = False
                    if widget_frame is not None:
                        was_not_launched = not widget.get("launched", False)
                        widget_ready = check_widget_ready(widget_frame)
                        if widget_ready:
                            widget["launched"] = True
                            if widget_height == 160 and was_not_launched:
                                small_widget_area = widget_frame.crop((0, 128, widget_width, 160))
                                area_bytes = small_widget_area.tobytes()
                                widget["has_small_widget"] = any(byte != 0 for byte in area_bytes)
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
