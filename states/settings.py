"""Settings state: device info, controls, Bluetooth QR, and reset."""
import json
import logging
import os
import subprocess
import time
from PIL import Image, ImageDraw

from domain.app_context import AppContext
from network_utils import get_primary_ipv4
from runtime.bluetooth_identity import (
    resolve_bluetooth_device_id,
    resolve_bluetooth_local_name,
)
from runtime.bluetooth_qr import (
    create_connection_qr_surface as _create_connection_qr_surface,
)
import runtime.machine_api as machine_api
from runtime.remote_sync_port import get_remote_sync
from runtime.sync.outcomes import OUTCOME_ACKED, OUTCOME_ERROR
from states.base import BaseState
from runtime.brightness import (
    clamp_brightness_level,
    raw_brightness_for_level,
    save_calibration,
    load_calibration_state,
)

try:
    from supabase_sync_bridge import (
        get_supabase_rest_latency_ms,
        get_supabase_rest_probe_ok,
    )
except ImportError:
    def get_supabase_rest_latency_ms():
        return None

    def get_supabase_rest_probe_ok():
        return False

_log = logging.getLogger(__name__)

SUPABASE_LATENCY_WARN_MS = 600


def _rssi_to_color(rssi):
    """Map RSSI (int or None) to (R, G, B).

    - None: no WiFi RSSI available / not connected -> gray
    - rssi >= -65: strong/acceptable WiFi -> green
    - otherwise: weak/poor WiFi -> red
    """
    if rssi is None:
        return (128, 128, 128)
    if rssi >= -65:
        return (0, 255, 0)
    return (255, 0, 0)


def _latency_to_color(ms):
    """Map Supabase REST round-trip (ms) to (R, G, B)."""
    if ms is None:
        return (128, 128, 128)
    if ms <= SUPABASE_LATENCY_WARN_MS:
        return (0, 255, 0)
    return (255, 0, 0)


def _bridge_active_wifi_icon_color(ctx):
    """Cloud-aware WiFi icon when the Supabase bridge process is running."""
    if not ctx.wifi_connected:
        return (128, 128, 128)
    sync = get_remote_sync()
    if not sync.is_connected() or not get_supabase_rest_probe_ok():
        return (255, 0, 0)
    latency_ms = get_supabase_rest_latency_ms()
    if latency_ms is None:
        return (128, 128, 128)
    return _latency_to_color(latency_ms)


def _settings_wifi_icon_color(ctx):
    if get_remote_sync().is_bridge_active():
        return _bridge_active_wifi_icon_color(ctx)
    return _rssi_to_color(machine_api.get_wifi_rssi_cached())



def _tint_icon_rgba(icon_rgba, color):
    """Return a new RGBA image with icon shape tinted to (R, G, B)."""
    r, g, b, a = icon_rgba.split()
    size = icon_rgba.size
    R = Image.new("L", size, color[0])
    G = Image.new("L", size, color[1])
    B = Image.new("L", size, color[2])
    return Image.merge("RGBA", (R, G, B, a))


VOLUME_LEVEL_VALUES = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def _clamp(value, min_value, max_value):
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _volume_raw_to_level(volume_raw):
    """Map raw volume (any int) to nearest level 0–10."""
    try:
        value = int(volume_raw)
    except (TypeError, ValueError):
        value = 50
    value = _clamp(value, VOLUME_LEVEL_VALUES[0], VOLUME_LEVEL_VALUES[-1])
    closest_index = 0
    smallest_diff = abs(VOLUME_LEVEL_VALUES[0] - value)
    for idx, v in enumerate(VOLUME_LEVEL_VALUES[1:], start=1):
        diff = abs(v - value)
        if diff < smallest_diff:
            smallest_diff = diff
            closest_index = idx
    return closest_index


def _volume_level_to_raw(level):
    """Map volume level (0–10) to canonical raw volume."""
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 5
    level_int = _clamp(level_int, 0, len(VOLUME_LEVEL_VALUES) - 1)
    return VOLUME_LEVEL_VALUES[level_int]


SETTINGS_ITEMS = [
    {"name": "Name", "type": "info"},
    {"name": "IP", "type": "info"},
    {"name": "Version", "type": "info"},
    {"name": "Volume", "type": "value"},
    {"name": "Brightness", "type": "value"},
    {"name": "Connection", "type": "action"},
    {"name": "System", "type": "action"},
]

CONNECTIONS_ITEMS = [
    {"name": "App Connection", "type": "action"},
    {"name": "Controllers", "type": "action"},
    {"name": "WiFi", "type": "action"},
]

MAINTENANCE_ITEMS = [
    {"name": "Calibration", "type": "action"},
    {"name": "Reset device", "type": "action"},
]

SETTINGS_ITEM_HEIGHT = 18
SETTINGS_INFO_TTL_SECONDS = 2.0
SETTINGS_NAME_INDEX = 0
SETTINGS_IP_INDEX = 1
SETTINGS_VERSION_INDEX = 2
SETTINGS_VOLUME_INDEX = 3
SETTINGS_BRIGHTNESS_INDEX = 4
SETTINGS_CONNECTIONS_INDEX = 5
SETTINGS_MAINTENANCE_INDEX = 6
SETTINGS_INFO_INDEXES = frozenset(
    (SETTINGS_NAME_INDEX, SETTINGS_IP_INDEX, SETTINGS_VERSION_INDEX)
)
# The info rows are read-only, so selection starts at Volume.
SETTINGS_FIRST_SELECTABLE_INDEX = SETTINGS_VOLUME_INDEX
SETTINGS_LAST_SELECTABLE_INDEX = len(SETTINGS_ITEMS) - 1
SETTINGS_MAINTENANCE_CALIBRATION_INDEX = 0
SETTINGS_MAINTENANCE_RESET_INDEX = 1
CONNECTIONS_APP_INDEX = 0
CONNECTIONS_CONTROLLERS_INDEX = 1
CONNECTIONS_WIFI_INDEX = 2
CONTROLLER_VISIBLE_ROWS = 7
WIFI_VISIBLE_ROWS = 7
WIFI_PASSWORD_LENGTH = 63
WIFI_CHARACTER_REPEAT_DELAY_SECONDS = 0.5
WIFI_CHARACTER_REPEAT_INTERVAL_SECONDS = 0.1
WIFI_PASSWORD_FREQUENT_SPECIALS = "!@#$%&*_-+=.?/"
_WIFI_PASSWORD_PRIORITY_CHARACTERS = (
    " "
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    + WIFI_PASSWORD_FREQUENT_SPECIALS
)
WIFI_PASSWORD_CHARACTERS = _WIFI_PASSWORD_PRIORITY_CHARACTERS + "".join(
    char
    for char in (chr(code) for code in range(32, 127))
    if char not in _WIFI_PASSWORD_PRIORITY_CHARACTERS
)
WIFI_SIGNAL_LEVELS = (34, 67)
WIFI_SIGNAL_COLORS = {
    1: (255, 0, 0),
    2: (255, 215, 0),
    3: (0, 255, 0),
}

_PAGE_SETTINGS = "settings"
_PAGE_CONNECTIONS = "connections"
_PAGE_CONTROLLERS = "controllers"
_PAGE_WIFI = "wifi"
_PAGE_WIFI_PASSWORD = "wifi_password"
_PAGE_MAINTENANCE = "maintenance"
_OVERLAY_BLUETOOTH_QR = "bluetooth_qr"
_OVERLAY_RESET_CONFIRM = "reset_confirm"
_OVERLAY_WIFI_FORGET_CONFIRM = "wifi_forget_confirm"
_OVERLAY_CALIBRATION_WARNING = "calibration_warning"
_OVERLAY_CALIBRATION_SUCCESS = "calibration_success"


def _draw_settings_label(draw, x, y, label, fill, font):
    """Draw uppercase words with explicit gaps because font8 has no space glyph."""
    current_x = x
    for word_index, word in enumerate(label.upper().split()):
        if word_index:
            current_x += 4
        draw.text((current_x, y), word, fill=fill, font=font)
        current_x += len(word) * 6



def _truncate_to_width(draw, text, font, max_width):
    """Trim `text` with a trailing ellipsis until it fits `max_width` pixels."""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textbbox((0, 0), trimmed + "...", font=font)[2] > max_width:
        trimmed = trimmed[:-1]
    # Nothing fits once even the ellipsis is too wide; draw nothing rather
    # than overflow the row with an 18px "...".
    return trimmed + "..." if trimmed else ""


def _controller_name(name):
    return str(name or "").strip() or "Controller"


def _controller_display_label(name, max_chars=18):
    """Fit a static controller name without exposing its MAC."""
    clean_name = _controller_name(name)
    if len(clean_name) <= max_chars:
        return clean_name
    if max_chars <= 3:
        return clean_name[:max_chars]
    return clean_name[: max_chars - 3] + "..."


def _controller_marquee_label(name, elapsed, max_chars=18):
    """Return a looping marquee window after a short initial pause."""
    clean_name = _controller_name(name)
    if len(clean_name) <= max_chars:
        return clean_name
    if elapsed < 1.0:
        return clean_name[:max_chars]
    cycle = clean_name + "   "
    offset = int((elapsed - 1.0) / 0.25) % len(cycle)
    repeats = ((offset + max_chars) // len(cycle)) + 2
    return (cycle * repeats)[offset : offset + max_chars]


def _read_release_version():
    """Release tag on the 'release' branch, otherwise a placeholder version."""
    try:
        branch = (
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
        )
        if branch == "release":
            return (
                subprocess.run(
                    ["git", "describe", "--tags", "--abbrev=0"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                .stdout.strip()
            )
    except Exception:
        pass
    return "v100.0.0"


def _read_device_name():
    """Read device.json['name'] fresh; a remote rename can change it at any time."""
    try:
        device_path = os.path.join(os.getcwd(), "device.json")
        with open(device_path, "r", encoding="utf-8") as f:
            device_data = json.load(f)
        return device_data.get("name", "-") or "-"
    except Exception:
        return "-"



class SettingsState(BaseState):
    """Settings root list with nested connections and maintenance pages."""

    def __init__(self):
        self._page_mode = _PAGE_SETTINGS
        self._overlay_mode = None
        self._bluetooth_qr_surface = None
        self._bluetooth_qr_key = None
        self._info_values_cache = None
        self._info_values_read_at = 0.0
        self._maintenance_selected_index = 0
        self._connections_select_index = 0
        self._controller_selected_key = "scan"
        self._controller_selected_index = 0
        self._controller_scroll_key = None
        self._controller_scroll_started_at = 0.0
        self._wifi_selected_index = 0
        self._wifi_selected_key = "rescan"
        self._wifi_scroll_key = None
        self._wifi_scroll_started_at = 0.0
        self._wifi_selected_network = None
        self._wifi_connection_uses_saved_profile = False
        self._wifi_forget_network = None
        self._wifi_password = [" "] * WIFI_PASSWORD_LENGTH
        self._wifi_cursor_index = 0
        self._wifi_character_repeat_direction = 0
        self._wifi_character_repeat_next_at = None
        self._calibration = None
        self._calibration_notice = ""
        self._reset_request_ref = None
        self._reset_request_failed = False

    def settings_info_values(self):
        """Return (name, ip, version), re-read at most once per TTL.

        Each read forks `ip` and up to two `git` processes, and the display loop
        renders at 30 FPS, so caching keeps scrolling from spawning a process per
        frame. The TTL is short enough that a remote rename still appears while
        this page is open.
        """
        now = time.monotonic()
        cached, read_at = self._info_values_cache, self._info_values_read_at
        if cached is not None and now - read_at < SETTINGS_INFO_TTL_SECONDS:
            return cached
        values = (_read_device_name(), get_primary_ipv4(), _read_release_version())
        self._info_values_cache = values
        self._info_values_read_at = now
        return values

    def name(self) -> str:
        return "settings"

    def consumes_btn_b_for_overlay(self, ctx: AppContext) -> bool:
        return self._overlay_mode is not None

    def _render_current_page(self, ctx: AppContext):
        if self._page_mode == _PAGE_CONNECTIONS:
            return self._render_connections(ctx)
        if self._page_mode == _PAGE_CONTROLLERS:
            return self._render_controllers(ctx)
        if self._page_mode == _PAGE_WIFI:
            self._consume_wifi_connection_result(ctx)
            self._consume_wifi_forget_result(ctx)
            return self._render_wifi(ctx)
        if self._page_mode == _PAGE_WIFI_PASSWORD:
            self._consume_wifi_connection_result(ctx)
            if self._page_mode == _PAGE_WIFI:
                return self._render_wifi(ctx)
            return self._render_wifi_password(ctx)
        if self._page_mode == _PAGE_MAINTENANCE:
            return self._render_maintenance(ctx)
        return self._render_settings(ctx)

    def update(self, ctx: AppContext) -> None:
        settings_image = self._render_current_page(ctx)

        if self._overlay_mode == _OVERLAY_BLUETOOTH_QR:
            self._refresh_bluetooth_qr(ctx)
            settings_image = self._render_bluetooth_qr_overlay(ctx, settings_image)
        elif self._overlay_mode == _OVERLAY_RESET_CONFIRM:
            self._consume_reset_request_result(ctx)
            if self._overlay_mode == _OVERLAY_RESET_CONFIRM:
                settings_image = self._render_reset_overlay(ctx, settings_image)
            else:
                settings_image = self._render_current_page(ctx)
        elif self._overlay_mode == _OVERLAY_WIFI_FORGET_CONFIRM:
            settings_image = self._render_wifi_forget_overlay(ctx, settings_image)
        elif self._overlay_mode == _OVERLAY_CALIBRATION_WARNING:
            settings_image = self._render_calibration_warning(ctx, settings_image)
        elif self._overlay_mode == _OVERLAY_CALIBRATION_SUCCESS:
            settings_image = self._render_calibration_success(ctx, settings_image)
        ctx.display.update_frame_buffer(settings_image)

    def _render_settings(self, ctx):
        settings_image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(settings_image)
        device_info = {}
        try:
            device_info = ctx.get_device_info()
            brightness = clamp_brightness_level(device_info.get("brightness", 5))
            volume = int(device_info.get("volume", 50))
        except Exception:
            brightness = 5
            volume = 50
        font8 = ctx.assets.font8
        selected_index = self._selected_root_index(ctx)
        for idx, item in enumerate(SETTINGS_ITEMS):
            y = idx * SETTINGS_ITEM_HEIGHT
            if idx in SETTINGS_INFO_INDEXES:
                # Read-only rows never take the highlight; they show a value.
                self._draw_settings_value_row(
                    settings_image, draw, ctx, idx, item, y
                )
                continue
            self._draw_row(
                draw,
                y,
                item["name"],
                idx == selected_index,
                font8,
            )
            if idx == SETTINGS_BRIGHTNESS_INDEX:
                self._draw_level_boxes(
                    draw, y, SETTINGS_ITEM_HEIGHT, 10, brightness
                )
            elif idx == SETTINGS_VOLUME_INDEX:
                volume_level = _volume_raw_to_level(volume)
                self._draw_level_boxes(
                    draw, y, SETTINGS_ITEM_HEIGHT, 10, volume_level
                )
        self._draw_footer(settings_image, draw, ctx, "SETTINGS")
        return settings_image

    def _draw_settings_value_row(self, image, draw, ctx, idx, item, y):
        """Draw one read-only info row: label on the left, value on the right."""
        font8 = ctx.assets.font8
        ty = y + (SETTINGS_ITEM_HEIGHT - 8) // 2
        device_name, ip_address, version = self.settings_info_values()
        _draw_settings_label(draw, 2, ty, item["name"], (255, 255, 255), font8)

        if idx == SETTINGS_IP_INDEX:
            wifi_icon = ctx.assets.wifi_icon
            color = _settings_wifi_icon_color(ctx)
            tinted = _tint_icon_rgba(wifi_icon.convert("RGBA"), color)
            icon_y = y + (SETTINGS_ITEM_HEIGHT - wifi_icon.size[1]) // 2
            image.paste(
                tinted.convert("RGB"), (2 + 12 + 2, icon_y), tinted.split()[3]
            )
            value, font = ip_address, font8
        elif idx == SETTINGS_VERSION_INDEX:
            value, font = version, font8
        else:
            font = ctx.assets.font_6x8
            value = _truncate_to_width(draw, device_name, font, 100)

        text_width = draw.textbbox((0, 0), value, font=font)[2]
        draw.text(
            (126 - text_width, ty),
            value,
            fill=(255, 255, 255),
            font=font,
        )

    def _render_maintenance(self, ctx):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = ctx.assets.font8
        device_info = ctx.get_device_info() or {}
        status = self._calibration.status if self._calibration is not None else None
        if status is not None and status.state == "running":
            return Image.new("RGB", (128, 160), (255, 255, 255))
        for idx, item in enumerate(MAINTENANCE_ITEMS):
            y = idx * SETTINGS_ITEM_HEIGHT
            color = self._draw_row(
                draw,
                y,
                item["name"],
                idx == self._maintenance_selected_index,
                font,
            )
            if item["name"] == "Calibration":
                calibration = load_calibration_state(device_info)
                label = "DONE" if calibration else "START"
                text_width = draw.textbbox((0, 0), label, font=font)[2]
                draw.text(
                    (126 - text_width, y + (SETTINGS_ITEM_HEIGHT - 8) // 2),
                    label,
                    fill=color,
                    font=font,
                )
        # Below two 18px rows of list, above the footer band.
        if self._calibration_notice:
            self._draw_centered_settings_label(
                draw, 56, self._calibration_notice[:20], (255, 160, 0), font
            )
        self._draw_footer(image, draw, ctx, "SYSTEM")
        return image

    def _render_calibration_warning(self, ctx, background):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = ctx.assets.font8
        accent = (255, 101, 140)

        # Hardware notice: one strong band, then short centered instructions.
        draw.rectangle((0, 0, 127, 18), fill=accent)
        self._draw_centered_settings_label(
            draw, 5, "SCREEN CALIBRATION", (0, 0, 0), font
        )
        draw.polygon(((64, 25), (54, 45), (74, 45)), fill=accent)
        draw.rectangle((63, 31, 64, 38), fill=(0, 0, 0))
        draw.rectangle((63, 41, 64, 42), fill=(0, 0, 0))

        for y, line in (
            (52, "UP TO 7.6 HOURS"),
            (67, "SCREEN GETS BRIGHT"),
            (82, "REMOVE ALL DARTS"),
            (97, "BEFORE START"),
        ):
            self._draw_centered_settings_label(draw, y, line, (255, 255, 255), font)

        draw.line((14, 115, 113, 115), fill=(96, 96, 96), width=1)
        self._draw_centered_settings_label(
            draw, 120, "A START   B BACK", (180, 180, 180), font
        )
        self._draw_footer(image, draw, ctx, "SYSTEM")
        return image

    def _render_calibration_success(self, ctx, background):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = ctx.assets.font8
        accent = (68, 214, 127)

        draw.rectangle((0, 0, 127, 18), fill=accent)
        self._draw_centered_settings_label(
            draw, 5, "SCREEN CALIBRATION", (0, 0, 0), font
        )

        draw.ellipse((50, 25, 78, 53), outline=accent, width=2)
        draw.line((57, 39, 62, 44), fill=accent, width=2)
        draw.line((62, 44, 72, 33), fill=accent, width=2)

        for y, line in (
            (63, "CALIBRATION"),
            (78, "COMPLETED"),
            (93, "SUCCESSFULLY"),
        ):
            self._draw_centered_settings_label(draw, y, line, (255, 255, 255), font)

        draw.line((14, 115, 113, 115), fill=(96, 96, 96), width=1)
        self._draw_centered_settings_label(
            draw, 120, "A MAIN MENU", (180, 180, 180), font
        )
        self._draw_footer(image, draw, ctx, "SYSTEM")
        return image

    @staticmethod
    def _draw_centered_settings_label(draw, y, label, fill, font):
        words = str(label).upper().split()
        if not words:
            return
        text_width = sum(len(word) * 6 for word in words) + 4 * (len(words) - 1)
        _draw_settings_label(draw, max(0, (128 - text_width) // 2), y, label, fill, font)

    def _start_calibration(self, ctx):
        if self._calibration is not None and self._calibration.status.state == "running":
            return
        from runtime.brightness_calibration import BrightnessCalibration

        device_info = ctx.get_device_info() or {}
        prior = clamp_brightness_level(device_info.get("brightness", 5))
        prior_raw = raw_brightness_for_level(prior, device_info)
        dart_reader = getattr(ctx, "get_raw_dart_bytes", None) or getattr(ctx, "get_darts", lambda: [])

        def on_success(values):
            save_calibration(values, device_info, current_level=prior)
            ctx.set_brightness_hardware(values[prior])
            self._calibration_notice = "SAVED"
            self._overlay_mode = _OVERLAY_CALIBRATION_SUCCESS

        self._calibration = BrightnessCalibration(
            frame_writer=ctx.display.update_frame_buffer,
            brightness_setter=ctx.set_brightness_hardware,
            dart_reader=dart_reader,
            on_success=on_success,
        )
        self._calibration.start(prior_raw)

    def _handle_maintenance_input(self, ctx, buttons):
        status = self._calibration.status if self._calibration is not None else None
        if status is not None and status.state == "running":
            if buttons.get("btn_b") or buttons.get("btn_home"):
                self._calibration.cancel()
            return
        if buttons.get("btn_a"):
            if self._maintenance_selected_index == SETTINGS_MAINTENANCE_CALIBRATION_INDEX:
                self._overlay_mode = _OVERLAY_CALIBRATION_WARNING
            elif self._maintenance_selected_index == SETTINGS_MAINTENANCE_RESET_INDEX:
                self._close_reset_overlay()
                self._overlay_mode = _OVERLAY_RESET_CONFIRM
            return
        if buttons.get("btn_up"):
            self._maintenance_selected_index = max(
                0, self._maintenance_selected_index - 1
            )
        elif buttons.get("btn_down"):
            self._maintenance_selected_index = min(
                len(MAINTENANCE_ITEMS) - 1, self._maintenance_selected_index + 1
            )


    def _render_connections(self, ctx):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        self._draw_simple_rows(
            draw,
            CONNECTIONS_ITEMS,
            self._connections_select_index,
            ctx.assets.font8,
        )
        self._draw_footer(image, draw, ctx, "CONNECTION")
        return image

    def _render_controllers(self, ctx):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        snapshot = self._controller_snapshot(ctx)
        rows = self._controller_rows(snapshot)
        selected_index = self._sync_controller_selection(rows)
        start = max(0, selected_index - CONTROLLER_VISIBLE_ROWS + 1)
        start = min(start, max(0, len(rows) - CONTROLLER_VISIBLE_ROWS))
        visible = rows[start : start + CONTROLLER_VISIBLE_ROWS]
        for visible_index, row in enumerate(visible):
            actual_index = start + visible_index
            y = visible_index * SETTINGS_ITEM_HEIGHT
            focused = actual_index == selected_index
            text_color = self._draw_row_background(draw, y, focused)
            if row["type"] == "scan":
                label = "SCANNING" if snapshot.get("is_scan") else "SCAN"
                _draw_settings_label(
                    draw,
                    2,
                    y + (SETTINGS_ITEM_HEIGHT - 8) // 2,
                    label,
                    text_color,
                    ctx.assets.font8,
                )
                if snapshot.get("is_scan"):
                    self._draw_activity_icon(
                        draw, 119, y + SETTINGS_ITEM_HEIGHT // 2
                    )
                continue
            label = self._controller_row_label(row, focused)
            draw.text(
                (2, y + (SETTINGS_ITEM_HEIGHT - 8) // 2),
                label,
                fill=text_color,
                font=ctx.assets.font_6x8,
            )
            self._draw_controller_status_icon(
                draw,
                row.get("status"),
                119,
                y + SETTINGS_ITEM_HEIGHT // 2,
            )
        self._draw_footer(image, draw, ctx, "CONTROLLERS")
        return image

    def _render_wifi(self, ctx):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.fontmode = "1"
        snapshot = self._wifi_snapshot(ctx)
        rows = self._wifi_rows(snapshot)
        selected_index = self._sync_wifi_selection(rows)
        start = max(0, selected_index - WIFI_VISIBLE_ROWS + 1)
        start = min(start, max(0, len(rows) - WIFI_VISIBLE_ROWS))
        font = getattr(ctx.assets, "system_font10", ctx.assets.font_6x8)
        for visible_index, row in enumerate(rows[start : start + WIFI_VISIBLE_ROWS]):
            actual_index = start + visible_index
            y = visible_index * SETTINGS_ITEM_HEIGHT
            focused = actual_index == selected_index
            color = self._draw_row_background(draw, y, focused)
            if row["type"] in {"current", "remembered"}:
                dot_color = (0, 255, 0) if row["type"] == "current" else (96, 96, 96)
                self._draw_status_dot(
                    draw, 5, y + SETTINGS_ITEM_HEIGHT // 2, dot_color
                )
                self._draw_wifi_ssid_label(
                    image, row, focused, font, color, y, max_width=92, x=11
                )
                if "rssi" in row:
                    self._draw_wifi_signal_dots(
                        draw, row.get("rssi", 0), 124,
                        y + SETTINGS_ITEM_HEIGHT // 2,
                    )
                continue
            if row["type"] == "rescan":
                label = "SCANNING" if snapshot.get("is_scan") else "RESCAN"
                _draw_settings_label(
                    draw, 2, y + (SETTINGS_ITEM_HEIGHT - 8) // 2,
                    label, color, ctx.assets.font8,
                )
                if snapshot.get("is_scan"):
                    self._draw_activity_icon(draw, 119, y + SETTINGS_ITEM_HEIGHT // 2)
                continue
            self._draw_wifi_ssid_label(
                image, row, focused, font, color, y, max_width=101
            )
            self._draw_wifi_signal_dots(
                draw, row.get("rssi", 0), 124, y + SETTINGS_ITEM_HEIGHT // 2
            )
        if len(rows) == 1 and not snapshot.get("is_scan"):
            message = snapshot.get("scan_error") or "No networks"
            self._draw_centered_system_text(draw, message, 44, font, (160, 160, 160))
        self._draw_footer(image, draw, ctx, "WIFI")
        return image

    def _render_wifi_password(self, ctx):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.fontmode = "1"
        font = getattr(ctx.assets, "system_font10", ctx.assets.font_6x8)
        network = self._wifi_selected_network or {}
        ssid = str(network.get("ssid") or "")
        self._draw_centered_system_text(
            draw, self._fit_text_to_width(draw, ssid, font, 124), 5, font, (255, 255, 255)
        )
        if not self._wifi_connection_uses_saved_profile:
            draw.text((2, 27), "Password", fill=(180, 180, 180), font=font)
            input_top = 43
            input_bottom = 65
            draw.rectangle(
                (1, input_top, 126, input_bottom),
                outline=(255, 255, 255), width=1,
            )
            visible_start, visible_text = self._wifi_password_window(draw, font)
            text_bbox = draw.textbbox((0, 0), visible_text, font=font)
            text_y = input_top + max(
                1,
                (input_bottom - input_top + 1 - (text_bbox[3] - text_bbox[1]))
                // 2
                - text_bbox[1],
            )
            draw.text((4, text_y), visible_text, fill=(255, 255, 255), font=font)
            cursor_offset = self._wifi_cursor_index - visible_start
            prefix = visible_text[:cursor_offset]
            current = visible_text[cursor_offset : cursor_offset + 1] or " "
            cursor_x = 4 + draw.textlength(prefix, font=font)
            cursor_width = max(4, draw.textlength(current, font=font))
            draw.rectangle(
                (
                    int(cursor_x), input_bottom - 2,
                    min(125, int(cursor_x + cursor_width)), input_bottom - 1,
                ),
                fill=(255, 101, 140),
            )
            index_text = f"{self._wifi_cursor_index + 1}/{WIFI_PASSWORD_LENGTH}"
            draw.text((2, 70), index_text, fill=(128, 128, 128), font=font)
        snapshot = self._wifi_snapshot(ctx)
        status = str(snapshot.get("connection_status") or "idle")
        if str(snapshot.get("connection_ssid") or "") != ssid:
            status = "idle"
        if status == "connecting":
            self._draw_centered_system_text(draw, "Connecting...", 91, font, (255, 255, 255))
            self._draw_activity_icon(draw, 64, 114)
        elif status == "error":
            self._draw_centered_system_text(
                draw, snapshot.get("connection_error") or "Unable to connect",
                91, font, (255, 80, 80),
            )
        elif not self._wifi_connection_uses_saved_profile:
            self._draw_centered_system_text(
                draw, "A: Connect", 91, font, (180, 180, 180)
            )
        self._draw_footer(image, draw, ctx, "WIFI")
        return image

    def _draw_wifi_ssid_label(
        self, image, row, focused, font, color, y, max_width, x=2
    ):
        """Draw an SSID in a clipped viewport, scrolling the focused long row."""
        ssid = str(row.get("ssid") or "")
        background = (255, 255, 255) if focused else (0, 0, 0)
        label_image = Image.new(
            "RGB", (max_width, SETTINGS_ITEM_HEIGHT), background
        )
        label_draw = ImageDraw.Draw(label_image)
        label_draw.fontmode = "1"
        full_width = label_draw.textlength(ssid, font=font)
        scrolling = focused and full_width > max_width

        if scrolling:
            key = str(row.get("key") or ssid)
            now = time.time()
            if key != self._wifi_scroll_key:
                self._wifi_scroll_key = key
                self._wifi_scroll_started_at = now
            elapsed = now - self._wifi_scroll_started_at
            gap = 18
            cycle_width = max(1, int(full_width) + gap)
            offset = (
                0 if elapsed < 1.0
                else int((elapsed - 1.0) / 0.05) % cycle_width
            )
            labels = ((ssid, -offset), (ssid, cycle_width - offset))
        else:
            label = self._fit_text_to_width(
                label_draw, ssid, font, max_width
            )
            labels = ((label, 0),)

        for label, text_x in labels:
            bbox = label_draw.textbbox((0, 0), label, font=font)
            text_y = max(
                0,
                (SETTINGS_ITEM_HEIGHT - (bbox[3] - bbox[1])) // 2
                - bbox[1],
            )
            label_draw.text((text_x, text_y), label, fill=color, font=font)
        image.paste(label_image, (x, y))

    @staticmethod
    def _fit_text_to_width(draw, text, font, max_width):
        value = str(text or "")
        if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
            return value
        suffix = "..."
        while value and draw.textbbox((0, 0), value + suffix, font=font)[2] > max_width:
            value = value[:-1]
        return value + suffix if value else suffix

    @staticmethod
    def _draw_centered_system_text(draw, text, y, font, fill):
        value = str(text or "")
        bbox = draw.textbbox((0, 0), value, font=font)
        draw.text(((128 - (bbox[2] - bbox[0])) / 2, y - bbox[1]), value, fill=fill, font=font)

    def _wifi_password_window(self, draw, font, max_width=118):
        """Return a cursor-centered slice that fits the variable-width system font."""
        cursor = self._wifi_cursor_index
        start = cursor
        end = cursor + 1
        prefer_left = True
        while start > 0 or end < WIFI_PASSWORD_LENGTH:
            candidates = []
            if start > 0:
                candidates.append(("left", start - 1, end))
            if end < WIFI_PASSWORD_LENGTH:
                candidates.append(("right", start, end + 1))
            if len(candidates) == 2 and not prefer_left:
                candidates.reverse()

            expanded = False
            for side, candidate_start, candidate_end in candidates:
                candidate = "".join(
                    self._wifi_password[candidate_start:candidate_end]
                )
                if draw.textlength(candidate, font=font) <= max_width:
                    start, end = candidate_start, candidate_end
                    prefer_left = side != "left"
                    expanded = True
                    break
            if not expanded:
                break
        return start, "".join(self._wifi_password[start:end])

    def _controller_row_label(self, row, focused):
        if not focused:
            return _controller_display_label(row.get("name"))
        key = row.get("key")
        now = time.time()
        if key != self._controller_scroll_key:
            self._controller_scroll_key = key
            self._controller_scroll_started_at = now
        return _controller_marquee_label(
            row.get("name"),
            now - self._controller_scroll_started_at,
        )

    @staticmethod
    def _draw_level_boxes(draw, y, item_height, dot_count, lit_count):
        dot_size = 5
        dot_gap = 1
        bar_width = dot_count * dot_size + (dot_count - 1) * dot_gap
        bar_left_x = 126 - bar_width + 1
        dot_top_y = y + (item_height - dot_size) // 2
        for i in range(dot_count):
            if i >= lit_count:
                continue
            dot_x0 = bar_left_x + i * (dot_size + dot_gap)
            draw.rectangle(
                (dot_x0, dot_top_y, dot_x0 + dot_size - 1, dot_top_y + dot_size - 1),
                fill=(255, 101, 140),
            )

    @staticmethod
    def _draw_simple_rows(draw, items, selected_index, font):
        for idx, item in enumerate(items):
            SettingsState._draw_row(
                draw,
                idx * SETTINGS_ITEM_HEIGHT,
                item["name"],
                idx == selected_index,
                font,
            )

    @staticmethod
    def _draw_row_background(draw, y, focused):
        """Paint the focus highlight and return the row's text color."""
        if focused:
            draw.rectangle(
                (0, y, 127, y + SETTINGS_ITEM_HEIGHT - 1),
                fill=(255, 255, 255),
            )
            return (0, 0, 0)
        return (255, 255, 255)

    @staticmethod
    def _draw_row(draw, y, label, focused, font):
        color = SettingsState._draw_row_background(draw, y, focused)
        _draw_settings_label(
            draw,
            2,
            y + (SETTINGS_ITEM_HEIGHT - 8) // 2,
            label,
            color,
            font,
        )
        return color


    @staticmethod
    def _draw_footer(image, draw, ctx, title):
        image.paste(
            ctx.assets.settings_icon,
            (24, 128),
            ctx.assets.settings_icon.convert("RGBA"),
        )
        # Footer titles sit on the left 64px panel, in the menu label face.
        width = len(title) * 6
        draw.text(
            (max(0, int((64 - width) / 2)), 152),
            title,
            fill=(255, 255, 255),
            font=ctx.assets.font8,
        )

    @staticmethod
    def _draw_activity_icon(draw, cx, cy):
        phase = int(time.time() * 6) % 4
        points = [(cx, cy - 4), (cx + 4, cy), (cx, cy + 4), (cx - 4, cy)]
        for index, (x, y) in enumerate(points):
            color = (255, 101, 140) if index == phase else (96, 96, 96)
            draw.rectangle((x - 1, y - 1, x + 1, y + 1), fill=color)

    @staticmethod
    def _draw_status_dot(draw, cx, cy, color):
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=color)

    @staticmethod
    def _wifi_signal_level(rssi):
        try:
            signal = int(rssi)
        except (TypeError, ValueError):
            signal = 0
        if signal >= WIFI_SIGNAL_LEVELS[1]:
            return 3
        if signal >= WIFI_SIGNAL_LEVELS[0]:
            return 2
        return 1

    def _draw_wifi_signal_dots(self, draw, rssi, right_x, center_y):
        level = self._wifi_signal_level(rssi)
        color = WIFI_SIGNAL_COLORS[level]
        first_x = right_x - (level - 1) * 7
        for index in range(level):
            self._draw_status_dot(draw, first_x + index * 7, center_y, color)

    def _draw_controller_status_icon(self, draw, status, cx, cy):
        status = str(status or "idle").lower()
        if status == "connecting":
            self._draw_activity_icon(draw, cx, cy)
            return
        colors = {
            "idle": (96, 96, 96),
            "connected": (0, 255, 0),
            "error": (255, 0, 0),
        }
        self._draw_status_dot(
            draw,
            cx,
            cy,
            colors.get(status, colors["idle"]),
        )

    def _render_bluetooth_qr_overlay(self, ctx, image):
        if self._bluetooth_qr_surface is not None:
            image.paste(self._bluetooth_qr_surface, (0, 0))
            return image
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 127, 127), fill=(0, 0, 0))
        for line, line_y in (("BLUETOOTH", 52), ("UNAVAILABLE", 68)):
            bbox = draw.textbbox((0, 0), line, font=ctx.assets.font_6x8)
            draw.text(
                ((128 - (bbox[2] - bbox[0])) / 2, line_y),
                line,
                fill=(255, 255, 255),
                font=ctx.assets.font_6x8,
            )
        return image

    def _reset_request_in_flight(self) -> bool:
        """True while the remote reset call is queued and unanswered."""
        return self._reset_request_ref is not None and not self._reset_request_failed

    def _close_reset_overlay(self) -> None:
        self._overlay_mode = None
        self._reset_request_ref = None
        self._reset_request_failed = False

    def _start_remote_reset(self, ctx) -> None:
        """Issue the remote reset and remember how to watch its outcome.

        An empty ref means the call could not even be queued (no bridge, no
        engine); with no local fallback left that is a failure like any other,
        so the overlay stays open rather than closing as if it had worked.
        """
        resolver = getattr(ctx, "reset_device", None)
        ref = ""
        if resolver is not None:
            try:
                ref = str(resolver() or "")
            except Exception as e:
                _log.warning("settings: reset request failed: %s", e)
                ref = ""
        self._reset_request_ref = ref or None
        self._reset_request_failed = not ref

    def _consume_reset_request_result(self, ctx) -> None:
        """Close the overlay once the call is acknowledged; keep it open on failure."""
        if self._reset_request_ref is None:
            return
        reader = getattr(ctx, "reset_ref_status", None)
        status = None
        if reader is not None:
            try:
                status = reader(self._reset_request_ref)
            except Exception as e:
                _log.warning("settings: reset status lookup failed: %s", e)
                status = None
        if status == OUTCOME_ACKED:
            self._close_reset_overlay()
        elif status == OUTCOME_ERROR:
            self._reset_request_failed = True

    def _render_reset_overlay(self, ctx, image):
        settings_rgba = image.convert("RGBA")
        overlay = Image.new("RGBA", (128, 160), (0, 0, 0, 180))
        settings_rgba.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(settings_rgba)
        font = getattr(ctx.assets, "system_font10", ctx.assets.font_6x8)
        if self._reset_request_failed:
            self._draw_centered_system_text(draw, "Reset failed", 48, font, (255, 80, 80))
            self._draw_centered_system_text(
                draw, "A: Retry  B: Close", 76, font, (180, 180, 180)
            )
        elif self._reset_request_in_flight():
            self._draw_centered_system_text(draw, "Resetting...", 48, font, "white")
            self._draw_activity_icon(draw, 64, 76)
        else:
            self._draw_centered_system_text(draw, "Reset device?", 48, font, "white")
            self._draw_centered_system_text(
                draw, "A: Confirm  B: Cancel", 76, font, (180, 180, 180)
            )
        return settings_rgba.convert("RGB")

    def _render_wifi_forget_overlay(self, ctx, image):
        settings_rgba = image.convert("RGBA")
        overlay = Image.new("RGBA", (128, 160), (0, 0, 0, 200))
        settings_rgba.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(settings_rgba)
        draw.fontmode = "1"
        font = getattr(ctx.assets, "system_font10", ctx.assets.font_6x8)
        network = self._wifi_forget_network or {}
        ssid = self._fit_text_to_width(
            draw, str(network.get("ssid") or "WiFi"), font, 120
        )
        snapshot = self._wifi_snapshot(ctx)
        status = str(snapshot.get("forget_status") or "idle")
        if status == "forgetting":
            self._draw_centered_system_text(draw, ssid, 42, font, "white")
            self._draw_centered_system_text(draw, "Forgetting...", 68, font, "white")
            self._draw_activity_icon(draw, 64, 92)
        elif status == "error":
            self._draw_centered_system_text(draw, ssid, 38, font, "white")
            self._draw_centered_system_text(
                draw, snapshot.get("forget_error") or "Unable to forget",
                64, font, (255, 80, 80),
            )
            self._draw_centered_system_text(
                draw, "B: Close", 88, font, (180, 180, 180)
            )
        else:
            self._draw_centered_system_text(draw, "Forget WiFi?", 34, font, "white")
            self._draw_centered_system_text(draw, ssid, 58, font, "white")
            self._draw_centered_system_text(
                draw, "A: Confirm  B: Cancel", 84, font, (180, 180, 180)
            )
        return settings_rgba.convert("RGB")

    @staticmethod
    def _controller_snapshot(ctx):
        manager = getattr(ctx, "bluetooth_controller", None)
        if manager is None:
            return {"is_scan": False, "controllers": [], "scan_results": []}
        try:
            snapshot = manager.get_state_snapshot()
            if isinstance(snapshot, dict):
                return snapshot
        except Exception:
            pass
        return {"is_scan": False, "controllers": [], "scan_results": []}

    @staticmethod
    def _normalized_controller_row(item, row_type):
        if not isinstance(item, dict):
            return None
        mac = str(item.get("mac") or item.get("address") or "").strip().upper()
        if not mac:
            return None
        status = str(item.get("status") or "idle").strip().lower()
        if status not in {"idle", "connecting", "connected", "error"}:
            status = "idle"
        return {
            "key": f"{row_type}:{mac}",
            "type": row_type,
            "mac": mac,
            "name": str(item.get("name") or "").strip(),
            "status": status,
        }

    def _controller_rows(self, snapshot):
        rows = []
        remembered_macs = set()
        for item in snapshot.get("controllers") or []:
            row = self._normalized_controller_row(item, "controller")
            if row is None or row["mac"] in remembered_macs:
                continue
            remembered_macs.add(row["mac"])
            rows.append(row)
        rows.append({"key": "scan", "type": "scan"})
        result_macs = set()
        for item in snapshot.get("scan_results") or []:
            row = self._normalized_controller_row(item, "result")
            if (
                row is None
                or row["mac"] in remembered_macs
                or row["mac"] in result_macs
            ):
                continue
            result_macs.add(row["mac"])
            rows.append(row)
        return rows

    def _sync_controller_selection(self, rows):
        keys = [row["key"] for row in rows]
        if self._controller_selected_key in keys:
            index = keys.index(self._controller_selected_key)
        else:
            index = min(self._controller_selected_index, len(rows) - 1)
            index = max(0, index)
            self._controller_selected_key = rows[index]["key"]
        self._controller_selected_index = index
        return index

    def _move_controller_selection(self, ctx, delta):
        rows = self._controller_rows(self._controller_snapshot(ctx))
        index = self._sync_controller_selection(rows)
        index = _clamp(index + delta, 0, len(rows) - 1)
        self._controller_selected_index = index
        self._controller_selected_key = rows[index]["key"]

    def _enter_controllers(self, ctx):
        self._page_mode = _PAGE_CONTROLLERS
        self._controller_selected_key = "scan"
        self._controller_selected_index = 0
        self._controller_scroll_key = None
        self._controller_scroll_started_at = 0.0
        manager = getattr(ctx, "bluetooth_controller", None)
        if manager is not None:
            try:
                manager.refresh_remembered_if_requested()
            except Exception:
                pass

    @staticmethod
    def _wifi_snapshot(ctx):
        manager = getattr(ctx, "wifi_controller", None)
        if manager is None:
            return {
                "is_scan": False, "networks": [], "connected_network": None,
                "remembered_networks": [], "scan_error": "",
                "connection_status": "idle",
                "connection_error": "",
                "connection_ssid": "",
            }
        try:
            snapshot = manager.get_state_snapshot()
            if isinstance(snapshot, dict):
                return snapshot
        except Exception:
            pass
        return {
            "is_scan": False, "networks": [], "connected_network": None,
            "remembered_networks": [], "scan_error": "",
            "connection_status": "idle",
            "connection_error": "",
            "connection_ssid": "",
        }

    @staticmethod
    def _wifi_rows(snapshot):
        rows = []
        connected = snapshot.get("connected_network")
        connected_ssid = ""
        if isinstance(connected, dict):
            connected_ssid = str(connected.get("ssid") or "")
            if connected_ssid:
                current = dict(connected)
                current.update(
                    {"type": "current", "key": f"current:{connected_ssid}"}
                )
                rows.append(current)
        for network in snapshot.get("remembered_networks") or []:
            if not isinstance(network, dict):
                continue
            ssid = str(network.get("ssid") or "")
            profile = str(network.get("profile") or "")
            if not ssid or not profile or ssid == connected_ssid:
                continue
            row = dict(network)
            row.update({"type": "remembered", "key": f"remembered:{profile}"})
            rows.append(row)
        rows.append({"type": "rescan", "key": "rescan"})
        remembered_ssids = {
            str(row.get("ssid") or "")
            for row in rows
            if row.get("type") == "remembered"
        }
        for network in snapshot.get("networks") or []:
            if not isinstance(network, dict):
                continue
            ssid = str(network.get("ssid") or "")
            if not ssid or ssid == connected_ssid or ssid in remembered_ssids:
                continue
            row = dict(network)
            row.update({"type": "network", "key": f"network:{ssid}"})
            rows.append(row)
        return rows

    def _sync_wifi_selection(self, rows):
        if not rows:
            self._wifi_selected_index = 0
            self._wifi_selected_key = ""
            return 0
        keys = [row.get("key") for row in rows]
        if self._wifi_selected_key in keys:
            self._wifi_selected_index = keys.index(self._wifi_selected_key)
        else:
            self._wifi_selected_index = _clamp(
                self._wifi_selected_index, 0, len(rows) - 1
            )
            self._wifi_selected_key = rows[self._wifi_selected_index]["key"]
        return self._wifi_selected_index

    def _move_wifi_selection(self, rows, delta):
        selected_index = self._sync_wifi_selection(rows)
        self._wifi_selected_index = _clamp(
            selected_index + delta, 0, len(rows) - 1
        )
        self._wifi_selected_key = rows[self._wifi_selected_index]["key"]

    def _enter_wifi(self, ctx):
        self._page_mode = _PAGE_WIFI
        self._wifi_selected_index = 0
        self._wifi_selected_key = "rescan"
        self._wifi_scroll_key = None
        self._wifi_scroll_started_at = 0.0
        manager = getattr(ctx, "wifi_controller", None)
        if manager is not None:
            try:
                manager.clear_connection_result()
                manager.start_scan_if_requested()
            except Exception:
                pass

    def _enter_wifi_password(self, network):
        self._page_mode = _PAGE_WIFI_PASSWORD
        self._wifi_selected_network = dict(network)
        self._wifi_connection_uses_saved_profile = False
        self._wifi_password = [" "] * WIFI_PASSWORD_LENGTH
        self._wifi_cursor_index = 0
        self._reset_wifi_character_repeat()

    def _enter_wifi_saved_connect(self, ctx, network):
        manager = getattr(ctx, "wifi_controller", None)
        if manager is None:
            return
        self._page_mode = _PAGE_WIFI_PASSWORD
        self._wifi_selected_network = dict(network)
        self._wifi_connection_uses_saved_profile = True
        self._reset_wifi_character_repeat()
        try:
            manager.clear_connection_result()
            manager.start_connect_saved_if_requested(
                str(network.get("profile") or ""),
                str(network.get("ssid") or ""),
            )
        except Exception:
            pass

    def _consume_wifi_connection_result(self, ctx):
        snapshot = self._wifi_snapshot(ctx)
        if snapshot.get("connection_status") != "success":
            return
        selected_ssid = str((self._wifi_selected_network or {}).get("ssid") or "")
        if str(snapshot.get("connection_ssid") or "") != selected_ssid:
            return
        self._page_mode = _PAGE_WIFI
        self._wifi_selected_index = 0
        self._wifi_selected_key = "rescan"
        manager = getattr(ctx, "wifi_controller", None)
        if manager is not None:
            try:
                manager.clear_connection_result()
                manager.start_scan_if_requested()
            except Exception:
                pass

    def _consume_wifi_forget_result(self, ctx):
        snapshot = self._wifi_snapshot(ctx)
        if snapshot.get("forget_status") != "success":
            return
        manager = getattr(ctx, "wifi_controller", None)
        if manager is not None:
            try:
                manager.clear_forget_result()
                manager.start_scan_if_requested()
            except Exception:
                pass
        self._overlay_mode = None
        self._wifi_forget_network = None
        self._wifi_selected_key = "rescan"

    def _refresh_bluetooth_qr(self, ctx):
        sync = get_remote_sync()
        connected = bool(sync.is_connected())
        try:
            device_info = ctx.get_device_info()
            local_name = resolve_bluetooth_local_name(device_info)
            device_id = resolve_bluetooth_device_id(device_info) or ""
        except Exception:
            local_name = None
            device_id = ""
        key = (connected, device_id, local_name)
        if key == self._bluetooth_qr_key:
            return
        self._bluetooth_qr_key = key
        try:
            self._bluetooth_qr_surface = _create_connection_qr_surface(
                local_name, supabase_connected=connected, device_id=device_id
            )
        except Exception:
            self._bluetooth_qr_surface = None

    def _open_bluetooth_qr(self, ctx):
        self._bluetooth_qr_surface = None
        self._bluetooth_qr_key = None
        self._refresh_bluetooth_qr(ctx)
        self._overlay_mode = _OVERLAY_BLUETOOTH_QR

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        if self._overlay_mode == _OVERLAY_BLUETOOTH_QR:
            if buttons.get("btn_b") or buttons.get("btn_home"):
                self._overlay_mode = None
                self._bluetooth_qr_surface = None
                self._bluetooth_qr_key = None
            return

        if self._overlay_mode == _OVERLAY_CALIBRATION_WARNING:
            if buttons.get("btn_a"):
                self._overlay_mode = None
                self._start_calibration(ctx)
            elif buttons.get("btn_b") or buttons.get("btn_home"):
                self._overlay_mode = None
            return

        if self._overlay_mode == _OVERLAY_CALIBRATION_SUCCESS:
            if buttons.get("btn_a"):
                self._overlay_mode = None
                self._transition_to_main_menu(ctx)
            return

        if self._overlay_mode == _OVERLAY_WIFI_FORGET_CONFIRM:
            snapshot = self._wifi_snapshot(ctx)
            status = str(snapshot.get("forget_status") or "idle")
            if status == "forgetting":
                return
            if buttons.get("btn_a") and status == "idle":
                manager = getattr(ctx, "wifi_controller", None)
                network = self._wifi_forget_network or {}
                if manager is not None:
                    try:
                        manager.start_forget_if_requested(
                            str(network.get("profile") or ""),
                            str(network.get("ssid") or ""),
                        )
                    except Exception:
                        pass
            elif buttons.get("btn_b") or buttons.get("btn_home"):
                manager = getattr(ctx, "wifi_controller", None)
                if manager is not None:
                    try:
                        manager.clear_forget_result()
                    except Exception:
                        pass
                self._overlay_mode = None
                self._wifi_forget_network = None
            return

        if self._overlay_mode == _OVERLAY_RESET_CONFIRM:
            # The overlay is closed by the request's outcome, not by the press:
            # a failed call leaves it open so the user can retry.
            if buttons.get("btn_a") and not self._reset_request_in_flight():
                self._start_remote_reset(ctx)
            elif buttons.get("btn_b") or buttons.get("btn_home"):
                if not self._reset_request_in_flight():
                    self._close_reset_overlay()
            return

        if buttons.get("btn_home"):
            if self._calibration is not None and self._calibration.status.state == "running":
                self._calibration.cancel()
                return
            self._transition_to_main_menu(ctx)
            return
        if buttons.get("btn_b"):
            if self._calibration is not None and self._calibration.status.state == "running":
                self._calibration.cancel()
                return
            if self._page_mode == _PAGE_MAINTENANCE:
                self._page_mode = _PAGE_SETTINGS
                ctx.setting_select_index = SETTINGS_MAINTENANCE_INDEX
            elif self._page_mode == _PAGE_CONTROLLERS:
                self._page_mode = _PAGE_CONNECTIONS
                self._connections_select_index = CONNECTIONS_CONTROLLERS_INDEX
            elif self._page_mode == _PAGE_WIFI_PASSWORD:
                self._reset_wifi_character_repeat()
                self._page_mode = _PAGE_WIFI
            elif self._page_mode == _PAGE_WIFI:
                self._page_mode = _PAGE_CONNECTIONS
                self._connections_select_index = CONNECTIONS_WIFI_INDEX
            elif self._page_mode == _PAGE_CONNECTIONS:
                self._page_mode = _PAGE_SETTINGS
                ctx.setting_select_index = SETTINGS_CONNECTIONS_INDEX
            else:
                self._transition_to_main_menu(ctx)
            return

        if self._page_mode == _PAGE_CONTROLLERS:
            self._handle_controllers_input(ctx, buttons)
        elif self._page_mode == _PAGE_WIFI:
            self._handle_wifi_input(ctx, buttons)
        elif self._page_mode == _PAGE_WIFI_PASSWORD:
            self._handle_wifi_password_input(ctx, buttons)
        elif self._page_mode == _PAGE_CONNECTIONS:
            self._handle_connections_input(ctx, buttons)
        elif self._page_mode == _PAGE_MAINTENANCE:
            self._handle_maintenance_input(ctx, buttons)
        else:
            self._handle_settings_input(ctx, buttons)

    @staticmethod
    def _transition_to_main_menu(ctx):
        from states.menu import MenuState

        ctx.transition_to(MenuState())

    def _handle_connections_input(self, ctx, buttons):
        if buttons.get("btn_a"):
            if self._connections_select_index == CONNECTIONS_APP_INDEX:
                self._open_bluetooth_qr(ctx)
            elif self._connections_select_index == CONNECTIONS_CONTROLLERS_INDEX:
                self._enter_controllers(ctx)
            else:
                self._enter_wifi(ctx)
        elif buttons.get("btn_up"):
            self._connections_select_index = max(
                0, self._connections_select_index - 1
            )
        elif buttons.get("btn_down"):
            self._connections_select_index = min(
                len(CONNECTIONS_ITEMS) - 1,
                self._connections_select_index + 1,
            )

    def _handle_wifi_input(self, ctx, buttons):
        snapshot = self._wifi_snapshot(ctx)
        rows = self._wifi_rows(snapshot)
        if buttons.get("btn_up"):
            self._move_wifi_selection(rows, -1)
            return
        if buttons.get("btn_down"):
            self._move_wifi_selection(rows, 1)
            return
        if not buttons.get("btn_a"):
            return
        selected = rows[self._sync_wifi_selection(rows)]
        manager = getattr(ctx, "wifi_controller", None)
        if selected["type"] == "current":
            if selected.get("profile"):
                self._wifi_forget_network = dict(selected)
                self._overlay_mode = _OVERLAY_WIFI_FORGET_CONFIRM
            return
        if selected["type"] == "remembered":
            self._enter_wifi_saved_connect(ctx, selected)
            return
        if selected["type"] == "rescan":
            if manager is not None:
                try:
                    manager.start_scan_if_requested()
                except Exception:
                    pass
            return
        if manager is not None:
            try:
                manager.clear_connection_result()
            except Exception:
                pass
        self._enter_wifi_password(selected)

    def _reset_wifi_character_repeat(self):
        self._wifi_character_repeat_direction = 0
        self._wifi_character_repeat_next_at = None

    def _cycle_wifi_password_character(self, delta, steps=1):
        current = self._wifi_password[self._wifi_cursor_index]
        try:
            index = WIFI_PASSWORD_CHARACTERS.index(current)
        except ValueError:
            index = 0
        self._wifi_password[self._wifi_cursor_index] = WIFI_PASSWORD_CHARACTERS[
            (index + delta * steps) % len(WIFI_PASSWORD_CHARACTERS)
        ]

    def _wifi_character_repeat(self, ctx, buttons):
        """Return direction/steps for an initial press or a due held repeat."""
        pressed_direction = 0
        if buttons.get("btn_up"):
            pressed_direction = 1
        elif buttons.get("btn_down"):
            pressed_direction = -1

        now = time.monotonic()
        if pressed_direction:
            self._wifi_character_repeat_direction = pressed_direction
            self._wifi_character_repeat_next_at = (
                now + WIFI_CHARACTER_REPEAT_DELAY_SECONDS
            )
            return pressed_direction, 1

        held = getattr(ctx, "current_button_state", None) or {}
        held_direction = 0
        if held.get("btn_up") and not held.get("btn_down"):
            held_direction = 1
        elif held.get("btn_down") and not held.get("btn_up"):
            held_direction = -1

        if held_direction != self._wifi_character_repeat_direction:
            self._reset_wifi_character_repeat()
            return 0, 0
        next_at = self._wifi_character_repeat_next_at
        if not held_direction or next_at is None or now < next_at:
            return 0, 0

        steps = int(
            (now - next_at + 1e-9)
            / WIFI_CHARACTER_REPEAT_INTERVAL_SECONDS
        ) + 1
        self._wifi_character_repeat_next_at = (
            next_at + steps * WIFI_CHARACTER_REPEAT_INTERVAL_SECONDS
        )
        return held_direction, steps

    def _handle_wifi_password_input(self, ctx, buttons):
        snapshot = self._wifi_snapshot(ctx)
        if self._wifi_connection_uses_saved_profile:
            return
        selected_ssid = str((self._wifi_selected_network or {}).get("ssid") or "")
        connection_matches = (
            str(snapshot.get("connection_ssid") or "") == selected_ssid
        )
        if connection_matches and snapshot.get("connection_status") == "connecting":
            self._reset_wifi_character_repeat()
            return
        if buttons.get("btn_left"):
            self._reset_wifi_character_repeat()
            self._wifi_cursor_index = max(0, self._wifi_cursor_index - 1)
        elif buttons.get("btn_right"):
            self._reset_wifi_character_repeat()
            self._wifi_cursor_index = min(
                WIFI_PASSWORD_LENGTH - 1, self._wifi_cursor_index + 1
            )
        elif buttons.get("btn_a"):
            self._reset_wifi_character_repeat()
            manager = getattr(ctx, "wifi_controller", None)
            network = self._wifi_selected_network or {}
            if manager is None or not network:
                return
            if connection_matches and snapshot.get("connection_status") == "error":
                try:
                    manager.clear_connection_result()
                except Exception:
                    pass
            password = "".join(self._wifi_password).strip()
            try:
                manager.start_connect_if_requested(
                    str(network.get("ssid") or ""),
                    password,
                    bool(network.get("secured")),
                )
            except Exception:
                pass
        else:
            direction, steps = self._wifi_character_repeat(ctx, buttons)
            if steps:
                self._cycle_wifi_password_character(direction, steps)

    def _handle_controllers_input(self, ctx, buttons):
        if buttons.get("btn_up"):
            self._move_controller_selection(ctx, -1)
            return
        if buttons.get("btn_down"):
            self._move_controller_selection(ctx, 1)
            return
        if not buttons.get("btn_a"):
            return

        snapshot = self._controller_snapshot(ctx)
        rows = self._controller_rows(snapshot)
        selected_index = self._sync_controller_selection(rows)
        row = rows[selected_index]
        manager = getattr(ctx, "bluetooth_controller", None)
        if manager is None:
            return
        if row["type"] == "scan":
            if snapshot.get("is_scan"):
                return
            try:
                manager.start_scan_if_requested()
            except Exception:
                pass
            return
        if row.get("status") in {"connected", "connecting"}:
            return
        source = "controllers" if row["type"] == "controller" else "scan_results"
        try:
            started = manager.start_connect_if_requested(row["mac"], source)
        except Exception:
            started = False
        if started and row["type"] == "result":
            self._controller_selected_key = f"controller:{row['mac']}"

    def _adjust_brightness(self, ctx, delta):
        info = ctx.get_device_info() or {}
        current = clamp_brightness_level(info.get("brightness", 5))
        target = max(0, min(9, current + delta))
        setter = getattr(ctx, "set_brightness_level", None)
        if setter is not None:
            setter(target)
        else:
            ctx.set_brightness(target)

    def _adjust_volume(self, ctx, delta):
        info = ctx.get_device_info() or {}
        try:
            raw_volume = int(info.get("volume", 50))
        except (TypeError, ValueError):
            raw_volume = 50
        level = _volume_raw_to_level(raw_volume)
        target = _clamp(level + delta, 0, len(VOLUME_LEVEL_VALUES) - 1)
        ctx.set_volume(_volume_level_to_raw(target))

    def _open_settings_row(self, ctx, idx):
        if idx == SETTINGS_CONNECTIONS_INDEX:
            self._page_mode = _PAGE_CONNECTIONS
            self._connections_select_index = CONNECTIONS_APP_INDEX
        elif idx == SETTINGS_MAINTENANCE_INDEX:
            self._page_mode = _PAGE_MAINTENANCE
            self._maintenance_selected_index = 0

    # Root rows whose value is adjustable in place with left/right.
    _ROOT_ROW_ADJUSTERS = {
        SETTINGS_BRIGHTNESS_INDEX: _adjust_brightness,
        SETTINGS_VOLUME_INDEX: _adjust_volume,
    }

    @staticmethod
    def _selected_root_index(ctx):
        """Clamp the shared cursor into the selectable range.

        `AppContext.setting_select_index` defaults to 0, which is the read-only
        Name row, so anything consuming it has to guard rather than trust it.
        """
        return _clamp(
            ctx.setting_select_index,
            SETTINGS_FIRST_SELECTABLE_INDEX,
            SETTINGS_LAST_SELECTABLE_INDEX,
        )

    def _handle_settings_input(self, ctx, buttons):
        idx = self._selected_root_index(ctx)
        if buttons.get("btn_a"):
            self._open_settings_row(ctx, idx)
        elif buttons.get("btn_left") or buttons.get("btn_right"):
            adjuster = self._ROOT_ROW_ADJUSTERS.get(idx)
            if adjuster is not None:
                adjuster(self, ctx, 1 if buttons.get("btn_right") else -1)
        elif buttons.get("btn_up"):
            ctx.setting_select_index = max(
                SETTINGS_FIRST_SELECTABLE_INDEX,
                idx - 1,
            )
        elif buttons.get("btn_down"):
            ctx.setting_select_index = min(
                SETTINGS_LAST_SELECTABLE_INDEX,
                idx + 1,
            )
