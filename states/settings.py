"""Settings state: device info, controls, Bluetooth QR, and reset."""
import json
import os
import re
import subprocess
import time
from PIL import Image, ImageDraw

from domain.app_context import AppContext
from network_utils import get_primary_ipv4
from runtime.bluetooth_identity import resolve_bluetooth_local_name
from runtime.bluetooth_qr import (
    create_bluetooth_qr_surface as _create_bluetooth_qr_surface,
)
from runtime.remote_sync_port import get_remote_sync
from states.base import BaseState

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

# Rate limit WiFi RSSI: refresh every ~5 seconds
RSSI_MIN_INTERVAL = 5
SUPABASE_LATENCY_WARN_MS = 600
_last_rssi = None
_last_rssi_time = 0.0


def _get_wifi_rssi_cached():
    """Return current WiFi RSSI (int dBm) or None if not connected. Rate-limited to 2/min."""
    global _last_rssi, _last_rssi_time
    now = time.time()
    if now - _last_rssi_time < RSSI_MIN_INTERVAL:
        return _last_rssi
    try:
        result = subprocess.run(
            ["iwconfig", "wlan0"],
            capture_output=True,
            text=True,
            check=True,
        )
        match = re.search(r"Signal level=(-\d+)", result.stdout)
        if match:
            _last_rssi = int(match.group(1))
        else:
            _last_rssi = None
    except Exception:
        _last_rssi = None
    _last_rssi_time = now
    return _last_rssi


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
    return _rssi_to_color(_get_wifi_rssi_cached())


def should_show_bridge_disconnect_icon(ctx) -> bool:
    """True when Supabase bridge is on and cloud health matches settings red WiFi."""
    if not get_remote_sync().is_bridge_active():
        return False
    if not ctx.wifi_connected:
        return False
    return _bridge_active_wifi_icon_color(ctx) == (255, 0, 0)


def _tint_icon_rgba(icon_rgba, color):
    """Return a new RGBA image with icon shape tinted to (R, G, B)."""
    r, g, b, a = icon_rgba.split()
    size = icon_rgba.size
    R = Image.new("L", size, color[0])
    G = Image.new("L", size, color[1])
    B = Image.new("L", size, color[2])
    return Image.merge("RGBA", (R, G, B, a))


BRIGHTNESS_LEVEL_VALUES = [10, 13, 16, 22, 32, 45, 61, 69, 80, 95]
BRIGHTNESS_LEVEL_VALUES_444F = [10, 13, 18, 22, 31, 42, 45, 58, 63, 80]
VOLUME_LEVEL_VALUES = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def _clamp(value, min_value, max_value):
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _brightness_values_for_device(device_info):
    version = str((device_info or {}).get("hardware_version", "")).strip().lower()
    if version == "444f":
        return BRIGHTNESS_LEVEL_VALUES_444F
    return BRIGHTNESS_LEVEL_VALUES


def _brightness_raw_to_level(brightness_raw):
    """Map raw brightness (any int) to nearest level 1-10."""
    try:
        value = int(brightness_raw)
    except (TypeError, ValueError):
        value = 50
    value = _clamp(value, BRIGHTNESS_LEVEL_VALUES[0], BRIGHTNESS_LEVEL_VALUES[-1])
    closest_index = 0
    smallest_diff = abs(BRIGHTNESS_LEVEL_VALUES[0] - value)
    for idx, v in enumerate(BRIGHTNESS_LEVEL_VALUES[1:], start=1):
        diff = abs(v - value)
        if diff < smallest_diff:
            smallest_diff = diff
            closest_index = idx
    return closest_index + 1


def _brightness_level_to_raw(level):
    """Map brightness level (1-10) to canonical raw brightness."""
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 5
    level_int = _clamp(level_int, 1, len(BRIGHTNESS_LEVEL_VALUES))
    return BRIGHTNESS_LEVEL_VALUES[level_int - 1]


def _brightness_raw_to_level_for_device(brightness_raw, device_info):
    values = _brightness_values_for_device(device_info)
    try:
        value = int(brightness_raw)
    except (TypeError, ValueError):
        value = 50
    value = _clamp(value, values[0], values[-1])
    closest_index = 0
    smallest_diff = abs(values[0] - value)
    for idx, v in enumerate(values[1:], start=1):
        diff = abs(v - value)
        if diff < smallest_diff:
            smallest_diff = diff
            closest_index = idx
    return closest_index + 1


def _brightness_level_to_raw_for_device(level, device_info):
    values = _brightness_values_for_device(device_info)
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 5
    level_int = _clamp(level_int, 1, len(values))
    return values[level_int - 1]


def _brightness_raw_to_display_boxes_for_device(brightness_raw, device_info):
    """Map 10 device brightness levels onto 9 UI boxes.

    The lowest device brightness level renders as 0 filled boxes, and each
    higher step fills one additional box.
    """
    level = _brightness_raw_to_level_for_device(brightness_raw, device_info)
    return _clamp(level - 1, 0, 9)


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
    {"name": "Brightness", "type": "value"},
    {"name": "Volume", "type": "value"},
    {"name": "Connectivity", "type": "action"},
    {"name": "Reset device", "type": "action"},
]

CONNECTIVITY_ITEMS = [
    {"name": "Bluetooth QR", "type": "action"},
    {"name": "Controllers", "type": "action"},
]

SETTINGS_LIST_MAX_HEIGHT = 128
SETTINGS_NUM_ROWS = 7
SETTINGS_ITEM_HEIGHT = SETTINGS_LIST_MAX_HEIGHT // SETTINGS_NUM_ROWS
SETTINGS_FIRST_SELECTABLE_INDEX = 3
SETTINGS_CONNECTIVITY_INDEX = 5
SETTINGS_RESET_DEVICE_INDEX = 6
CONTROLLER_VISIBLE_ROWS = 7

_PAGE_SETTINGS = "settings"
_PAGE_CONNECTIVITY = "connectivity"
_PAGE_CONTROLLERS = "controllers"
_OVERLAY_BLUETOOTH_QR = "bluetooth_qr"
_OVERLAY_RESET_CONFIRM = "reset_confirm"


def _draw_settings_label(draw, x, y, label, fill, font):
    """Draw uppercase words with explicit gaps because font8 has no space glyph."""
    current_x = x
    for word_index, word in enumerate(label.upper().split()):
        if word_index:
            current_x += 4
        draw.text((current_x, y), word, fill=fill, font=font)
        current_x += len(word) * 6


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


class SettingsState(BaseState):
    """Three-level settings menu with connectivity and controller management."""

    def __init__(self):
        self._page_mode = _PAGE_SETTINGS
        self._overlay_mode = None
        self._bluetooth_qr_surface = None
        self._connectivity_select_index = 0
        self._controller_selected_key = "scan"
        self._controller_selected_index = 0
        self._controller_scroll_key = None
        self._controller_scroll_started_at = 0.0

    def name(self) -> str:
        return "settings"

    def consumes_btn_b_for_overlay(self, ctx: AppContext) -> bool:
        return self._overlay_mode is not None

    def update(self, ctx: AppContext) -> None:
        if self._page_mode == _PAGE_CONNECTIVITY:
            settings_image = self._render_connectivity(ctx)
        elif self._page_mode == _PAGE_CONTROLLERS:
            settings_image = self._render_controllers(ctx)
        else:
            settings_image = self._render_settings(ctx)

        if self._overlay_mode == _OVERLAY_BLUETOOTH_QR:
            settings_image = self._render_bluetooth_qr_overlay(ctx, settings_image)
        elif self._overlay_mode == _OVERLAY_RESET_CONFIRM:
            settings_image = self._render_reset_overlay(ctx, settings_image)
        ctx.display.update_frame_buffer(settings_image)

    def _render_settings(self, ctx):
        settings_image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(settings_image)
        device_info = {}
        try:
            device_info = ctx.get_device_info()
            brightness = int(device_info.get("brightness", 50))
            volume = int(device_info.get("volume", 50))
        except Exception:
            brightness = 50
            volume = 50
        ip_address = get_primary_ipv4()
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
                version = (
                    subprocess.run(
                        ["git", "describe", "--tags", "--abbrev=0"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    .stdout.strip()
                )
            else:
                version = "v100.0.0"
        except Exception:
            version = "v100.0.0"
        try:
            device_path = os.path.join(os.getcwd(), "device.json")
            with open(device_path, "r", encoding="utf-8") as f:
                device_data = json.load(f)
            device_name = device_data.get("name", "-") or "-"
        except Exception:
            device_name = "-"

        item_height = SETTINGS_ITEM_HEIGHT
        text_y_offset = (item_height - 8) // 2
        font8 = ctx.assets.font8
        wifi_icon = ctx.assets.wifi_icon
        for idx, item in enumerate(SETTINGS_ITEMS):
            y = idx * item_height
            ty = y + text_y_offset
            focused = idx == ctx.setting_select_index
            text_color = (0, 0, 0) if focused else (255, 255, 255)
            if focused:
                draw.rectangle(
                    (0, y, 127, y + item_height - 1), fill=(255, 255, 255)
                )
            label_x = 2
            if item["name"] == "IP":
                color = _settings_wifi_icon_color(ctx)
                icon_rgba = wifi_icon.convert("RGBA")
                tinted = _tint_icon_rgba(icon_rgba, color)
                icon_y = y + (item_height - wifi_icon.size[1]) // 2
                icon_x = 2 + 12 + 2
                settings_image.paste(
                    tinted.convert("RGB"), (icon_x, icon_y), tinted.split()[3]
                )
            _draw_settings_label(
                draw, label_x, ty, item["name"], text_color, font8
            )
            if item["name"] == "Name":
                font_6x8 = ctx.assets.font_6x8
                max_width = 100
                display_name = device_name
                if draw.textbbox((0, 0), display_name, font=font_6x8)[2] > max_width:
                    suffix_text = "..."
                    while (
                        display_name
                        and draw.textbbox(
                            (0, 0), display_name + suffix_text, font=font_6x8
                        )[2]
                        > max_width
                    ):
                        display_name = display_name[:-1]
                    if display_name != device_name:
                        display_name += suffix_text
                text_width = draw.textbbox((0, 0), display_name, font=font_6x8)[2]
                draw.text(
                    (126 - text_width, ty),
                    display_name,
                    fill=text_color,
                    font=font_6x8,
                )
            elif item["name"] == "Brightness":
                brightness_level = _brightness_raw_to_display_boxes_for_device(
                    brightness, device_info
                )
                self._draw_level_boxes(draw, y, item_height, 9, brightness_level)
            elif item["name"] == "Volume":
                volume_level = _volume_raw_to_level(volume)
                self._draw_level_boxes(draw, y, item_height, 10, volume_level)
            elif item["name"] == "IP":
                text_width = len(ip_address) * 6
                draw.text(
                    (126 - text_width, ty),
                    ip_address,
                    fill=text_color,
                    font=font8,
                )
            elif item["name"] == "Version":
                text_width = len(version) * 6
                draw.text(
                    (126 - text_width, ty),
                    version,
                    fill=text_color,
                    font=font8,
                )
        self._draw_footer(settings_image, draw, ctx, "SETTINGS")
        return settings_image

    def _render_connectivity(self, ctx):
        image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        self._draw_simple_rows(
            draw,
            CONNECTIVITY_ITEMS,
            self._connectivity_select_index,
            ctx.assets.font8,
        )
        self._draw_footer(image, draw, ctx, "CONNECTIVITY")
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
            if focused:
                draw.rectangle(
                    (0, y, 127, y + SETTINGS_ITEM_HEIGHT - 1),
                    fill=(255, 255, 255),
                )
            text_color = (0, 0, 0) if focused else (255, 255, 255)
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
                    self._draw_status_dot(
                        draw,
                        119,
                        y + SETTINGS_ITEM_HEIGHT // 2,
                        (255, 101, 140),
                        pulse=True,
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
            y = idx * SETTINGS_ITEM_HEIGHT
            focused = idx == selected_index
            if focused:
                draw.rectangle(
                    (0, y, 127, y + SETTINGS_ITEM_HEIGHT - 1),
                    fill=(255, 255, 255),
                )
            color = (0, 0, 0) if focused else (255, 255, 255)
            _draw_settings_label(
                draw,
                2,
                y + (SETTINGS_ITEM_HEIGHT - 8) // 2,
                item["name"],
                color,
                font,
            )

    @staticmethod
    def _draw_footer(image, draw, ctx, title):
        image.paste(
            ctx.assets.settings_icon,
            (24, 128),
            ctx.assets.settings_icon.convert("RGBA"),
        )
        width = len(title) * 6
        draw.text(
            (max(0, int((64 - width) / 2)), 152),
            title,
            fill=(255, 255, 255),
            font=ctx.assets.font8,
        )

    @staticmethod
    def _draw_status_dot(draw, cx, cy, color, pulse=False):
        if pulse and int(time.time() * 4) % 2:
            color = tuple(max(32, channel // 3) for channel in color)
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=color)

    def _draw_controller_status_icon(self, draw, status, cx, cy):
        status = str(status or "idle").lower()
        colors = {
            "idle": (96, 96, 96),
            "connecting": (255, 101, 140),
            "connected": (0, 255, 0),
            "error": (255, 0, 0),
        }
        self._draw_status_dot(
            draw,
            cx,
            cy,
            colors.get(status, colors["idle"]),
            pulse=status == "connecting",
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

    @staticmethod
    def _render_reset_overlay(ctx, image):
        settings_rgba = image.convert("RGBA")
        overlay = Image.new("RGBA", (128, 160), (0, 0, 0, 180))
        settings_rgba.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(settings_rgba)
        line1 = "Reset device?"
        line2 = "A: Confirm  B: Cancel"
        bbox1 = draw.textbbox((0, 0), line1, font=ctx.assets.font24)
        bbox2 = draw.textbbox((0, 0), line2, font=ctx.assets.font_6x8)
        y1 = 64 - (bbox1[3] - bbox1[1]) - 4
        draw.text(
            ((128 - (bbox1[2] - bbox1[0])) / 2, y1),
            line1,
            fill="white",
            font=ctx.assets.font24,
        )
        draw.text(
            ((128 - (bbox2[2] - bbox2[0])) / 2, 76),
            line2,
            fill="white",
            font=ctx.assets.font_6x8,
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

    def _open_bluetooth_qr(self, ctx):
        self._bluetooth_qr_surface = None
        try:
            local_name = resolve_bluetooth_local_name(ctx.get_device_info())
            if local_name:
                self._bluetooth_qr_surface = _create_bluetooth_qr_surface(local_name)
        except Exception:
            self._bluetooth_qr_surface = None
        self._overlay_mode = _OVERLAY_BLUETOOTH_QR

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        if self._overlay_mode == _OVERLAY_BLUETOOTH_QR:
            if buttons.get("btn_b") or buttons.get("btn_home"):
                self._overlay_mode = None
                self._bluetooth_qr_surface = None
            return

        if self._overlay_mode == _OVERLAY_RESET_CONFIRM:
            if buttons.get("btn_a"):
                if ctx.reset_device:
                    ctx.reset_device()
                self._overlay_mode = None
            elif buttons.get("btn_b") or buttons.get("btn_home"):
                self._overlay_mode = None
            return

        if buttons.get("btn_home"):
            self._transition_to_main_menu(ctx)
            return
        if buttons.get("btn_b"):
            if self._page_mode == _PAGE_CONTROLLERS:
                self._page_mode = _PAGE_CONNECTIVITY
                self._connectivity_select_index = 1
            elif self._page_mode == _PAGE_CONNECTIVITY:
                self._page_mode = _PAGE_SETTINGS
                ctx.setting_select_index = SETTINGS_CONNECTIVITY_INDEX
            else:
                self._transition_to_main_menu(ctx)
            return

        if self._page_mode == _PAGE_CONTROLLERS:
            self._handle_controllers_input(ctx, buttons)
        elif self._page_mode == _PAGE_CONNECTIVITY:
            self._handle_connectivity_input(ctx, buttons)
        else:
            self._handle_settings_input(ctx, buttons)

    @staticmethod
    def _transition_to_main_menu(ctx):
        from states.menu import MenuState

        ctx.transition_to(MenuState())

    def _handle_connectivity_input(self, ctx, buttons):
        if buttons.get("btn_a"):
            if self._connectivity_select_index == 0:
                self._open_bluetooth_qr(ctx)
            else:
                self._enter_controllers(ctx)
        elif buttons.get("btn_up"):
            self._connectivity_select_index = max(
                0, self._connectivity_select_index - 1
            )
        elif buttons.get("btn_down"):
            self._connectivity_select_index = min(
                len(CONNECTIVITY_ITEMS) - 1,
                self._connectivity_select_index + 1,
            )

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

    def _handle_settings_input(self, ctx, buttons):
        if (
            buttons.get("btn_a")
            and ctx.setting_select_index == SETTINGS_CONNECTIVITY_INDEX
        ):
            self._page_mode = _PAGE_CONNECTIVITY
            self._connectivity_select_index = 0
        elif (
            buttons.get("btn_a")
            and ctx.setting_select_index == SETTINGS_RESET_DEVICE_INDEX
        ):
            self._overlay_mode = _OVERLAY_RESET_CONFIRM
        elif buttons.get("btn_left"):
            idx = ctx.setting_select_index
            di = ctx.get_device_info()
            if idx == 3:
                raw_brightness = int(di.get("brightness", "50"))
                level = _brightness_raw_to_level_for_device(raw_brightness, di)
                new_level = max(1, level - 1)
                ctx.set_brightness(
                    _brightness_level_to_raw_for_device(new_level, di)
                )
            elif idx == 4:
                raw_volume = int(di.get("volume", "50"))
                level = _volume_raw_to_level(raw_volume)
                ctx.set_volume(_volume_level_to_raw(max(0, level - 1)))
        elif buttons.get("btn_right"):
            idx = ctx.setting_select_index
            di = ctx.get_device_info()
            if idx == 3:
                raw_brightness = int(di.get("brightness", "50"))
                level = _brightness_raw_to_level_for_device(raw_brightness, di)
                new_level = min(len(_brightness_values_for_device(di)), level + 1)
                ctx.set_brightness(
                    _brightness_level_to_raw_for_device(new_level, di)
                )
            elif idx == 4:
                raw_volume = int(di.get("volume", "50"))
                level = _volume_raw_to_level(raw_volume)
                new_level = min(len(VOLUME_LEVEL_VALUES) - 1, level + 1)
                ctx.set_volume(_volume_level_to_raw(new_level))
        elif buttons.get("btn_up"):
            ctx.setting_select_index = max(
                SETTINGS_FIRST_SELECTABLE_INDEX,
                ctx.setting_select_index - 1,
            )
        elif buttons.get("btn_down"):
            ctx.setting_select_index = min(
                SETTINGS_RESET_DEVICE_INDEX,
                ctx.setting_select_index + 1,
            )
