"""Settings state: name, IP, version, brightness, volume."""
import json
import os
import re
import subprocess
import time
from PIL import Image, ImageDraw

from app_context import AppContext
from states.base import BaseState

# Rate limit WiFi RSSI: refresh every ~5 seconds
RSSI_MIN_INTERVAL = 5
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
    - rssi >= -60: strong/acceptable WiFi -> green
    - otherwise: weak/poor WiFi -> red
    """
    if rssi is None:
        return (128, 128, 128)
    if rssi >= -60:
        return (0, 255, 0)
    return (255, 0, 0)


def _tint_icon_rgba(icon_rgba, color):
    """Return a new RGBA image with icon shape tinted to (R, G, B)."""
    r, g, b, a = icon_rgba.split()
    size = icon_rgba.size
    R = Image.new("L", size, color[0])
    G = Image.new("L", size, color[1])
    B = Image.new("L", size, color[2])
    return Image.merge("RGBA", (R, G, B, a))


BRIGHTNESS_LEVEL_VALUES = [10, 20, 30, 40, 50, 59, 73, 79, 100]


def _clamp(value, min_value, max_value):
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _brightness_raw_to_level(brightness_raw):
    """Map raw brightness (any int) to nearest level 1–9."""
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
    """Map brightness level (1–9) to canonical raw brightness."""
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 5
    level_int = _clamp(level_int, 1, len(BRIGHTNESS_LEVEL_VALUES))
    return BRIGHTNESS_LEVEL_VALUES[level_int - 1]


SETTINGS_ITEMS = [
    {"name": "Name", "type": "info"},
    {"name": "IP", "type": "info"},
    {"name": "Version", "type": "info"},
    {"name": "Brightness", "type": "value"},
    {"name": "Volume", "type": "value"},
    {"name": "Reset device", "type": "action"},
]


# List height limited to 128px; 6 rows -> item_height 21
SETTINGS_LIST_MAX_HEIGHT = 128
SETTINGS_NUM_ROWS = 6
SETTINGS_ITEM_HEIGHT = SETTINGS_LIST_MAX_HEIGHT // SETTINGS_NUM_ROWS


class SettingsState(BaseState):
    """Settings menu: name, IP, version, brightness, volume, reset device; B/home back to menu."""

    def __init__(self):
        self._show_reset_confirm = False

    def name(self) -> str:
        return "settings"

    def consumes_btn_b_for_overlay(self, ctx: AppContext) -> bool:
        return self._show_reset_confirm

    def update(self, ctx: AppContext) -> None:
        settings_image = Image.new("RGB", (128, 160), (0, 0, 0))
        draw = ImageDraw.Draw(settings_image)
        try:
            device_info = ctx.get_device_info()
            brightness = int(device_info.get("brightness", 50))
            volume = int(device_info.get("volume", 50))
        except Exception:
            brightness = 50
            volume = 50
        try:
            ip_address = (
                subprocess.run(
                    ["hostname", "-I"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                .stdout.strip()
                .split()[0]
            )
        except Exception:
            ip_address = "0.0.0.0"
        try:
            version = (
                subprocess.run(
                    ["git", "describe", "--tags", "--abbrev=0"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                .stdout.strip()
            )
        except Exception:
            version = "v1.0.0"
        try:
            device_path = os.path.join(os.getcwd(), "device.json")
            with open(device_path, "r", encoding="utf-8") as f:
                device_data = json.load(f)
            device_name = device_data.get("name", "—") or "—"
        except Exception:
            device_name = "—"

        item_height = SETTINGS_ITEM_HEIGHT
        text_y_offset = (item_height - 8) // 2
        font8 = ctx.assets.font8
        wifi_icon = ctx.assets.wifi_icon
        for idx, item in enumerate(SETTINGS_ITEMS):
            y = idx * item_height
            ty = y + text_y_offset
            focused = idx == ctx.setting_select_index
            if focused:
                draw.rectangle(
                    (0, y, 127, y + item_height - 1), fill=(40, 40, 40)
                )
            if item["name"] == "IP":
                rssi = _get_wifi_rssi_cached()
                color = _rssi_to_color(rssi)
                icon_rgba = wifi_icon.convert("RGBA")
                tinted = _tint_icon_rgba(icon_rgba, color)
                icon_y = y + (item_height - wifi_icon.size[1]) // 2
                # "IP" is 2 chars @ 6px = 12; icon after label with 2px gap
                icon_x = 2 + 12 + 2
                settings_image.paste(
                    tinted.convert("RGB"), (icon_x, icon_y), tinted.split()[3]
                )
                label_x = 2
            else:
                label_x = 2
            if item["name"] == "Reset device":
                # font8 doesn't support space; draw two words with a gap
                draw.text((label_x, ty), "RESET", fill="white", font=font8)
                reset_w = 5 * 6  # 5 chars @ 6px
                gap = 4
                draw.text((label_x + reset_w + gap, ty), "DEVICE", fill="white", font=font8)
            else:
                draw.text(
                    (label_x, ty), item["name"].upper(), fill="white", font=font8
                )
            if item["name"] == "Name":
                font_6x8 = ctx.assets.font_6x8
                max_width = 100
                display_name = device_name
                if draw.textbbox((0, 0), display_name, font=font_6x8)[2] > max_width:
                    suffix = "..."
                    while display_name and draw.textbbox((0, 0), display_name + suffix, font=font_6x8)[2] > max_width:
                        display_name = display_name[:-1]
                    display_name = display_name + suffix if display_name != device_name else display_name
                text_width = draw.textbbox((0, 0), display_name, font=font_6x8)[2]
                value_x = 126 - text_width
                draw.text(
                    (value_x, ty),
                    display_name,
                    fill="white",
                    font=font_6x8,
                )
            elif item["name"] == "Brightness":
                brightness_level = _brightness_raw_to_level(brightness)
                value_str = f"{brightness_level}"
                value_width = len(value_str) * 6
                arrow_right_x = 104
                value_x = arrow_right_x - value_width
                arrow_left_x = value_x - 8
                if focused:
                    draw.text((arrow_left_x, ty), "<", fill="white", font=font8)
                    draw.text((arrow_right_x, ty), ">", fill="white", font=font8)
                draw.text((value_x, ty), value_str, fill="white", font=font8)
            elif item["name"] == "Volume":
                value_str = f"{volume}"
                value_width = len(value_str) * 6
                arrow_right_x = 104
                value_x = arrow_right_x - value_width
                arrow_left_x = value_x - 8
                if focused:
                    draw.text((arrow_left_x, ty), "<", fill="white", font=font8)
                    draw.text((arrow_right_x, ty), ">", fill="white", font=font8)
                draw.text((value_x, ty), value_str, fill="white", font=font8)
            elif item["name"] == "IP":
                text_width = len(ip_address) * 6
                value_x = 126 - text_width
                draw.text(
                    (value_x, ty),
                    ip_address,
                    fill="white",
                    font=font8,
                )
            elif item["name"] == "Version":
                text_width = len(version) * 6
                value_x = 126 - text_width
                draw.text(
                    (value_x, ty),
                    version,
                    fill="white",
                    font=font8,
                )
            elif item["name"] == "Reset device":
                pass  # label only, no value
        settings_image.paste(
            ctx.assets.settings_icon,
            (24, 128),
            ctx.assets.settings_icon.convert("RGBA"),
        )
        settings_text = "SETTINGS"
        settings_text_width = len(settings_text) * 6
        settings_text_x = int((64 - settings_text_width) / 2)
        draw.text(
            (settings_text_x, 152),
            settings_text,
            fill=(255, 255, 255),
            font=font8,
        )
        if self._show_reset_confirm:
            settings_rgba = settings_image.convert("RGBA")
            overlay = Image.new("RGBA", (128, 160), (0, 0, 0, 180))
            settings_rgba.paste(overlay, (0, 0), overlay)
            overlay_draw = ImageDraw.Draw(settings_rgba)
            font24 = ctx.assets.font24
            font_6x8 = ctx.assets.font_6x8
            line1 = "Reset device?"
            line2 = "A: Confirm  B: Cancel"
            bbox1 = overlay_draw.textbbox((0, 0), line1, font=font24)
            bbox2 = overlay_draw.textbbox((0, 0), line2, font=font_6x8)
            w1 = bbox1[2] - bbox1[0]
            w2 = bbox2[2] - bbox2[0]
            y1 = 64 - (bbox1[3] - bbox1[1]) - 4
            y2 = 64 + 12  # extra gap between "Reset device?" and second row
            overlay_draw.text(((128 - w1) / 2, y1), line1, fill="white", font=font24)
            overlay_draw.text(((128 - w2) / 2, y2), line2, fill="white", font=font_6x8)
            settings_image = settings_rgba.convert("RGB")
        ctx.display.update_frame_buffer(settings_image)

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        if self._show_reset_confirm:
            if buttons.get("btn_a"):
                if ctx.reset_device:
                    ctx.reset_device()
                self._show_reset_confirm = False
            elif buttons.get("btn_b") or buttons.get("btn_home"):
                self._show_reset_confirm = False
            return
        if buttons.get("btn_b") or buttons.get("btn_home"):
            from states.menu import MenuState
            ctx.transition_to(MenuState())
        elif buttons.get("btn_a") and ctx.setting_select_index == 5:
            self._show_reset_confirm = True
        elif buttons.get("btn_left"):
            idx = ctx.setting_select_index
            di = ctx.get_device_info()
            if idx == 3:
                raw_brightness = int(di.get("brightness", "50"))
                level = _brightness_raw_to_level(raw_brightness)
                new_level = max(1, level - 1)
                new_brightness = _brightness_level_to_raw(new_level)
                ctx.set_brightness(new_brightness)
            elif idx == 4:
                volume = max(int(di.get("volume", "50")) - 10, 0)
                ctx.set_volume(volume)
        elif buttons.get("btn_right"):
            idx = ctx.setting_select_index
            di = ctx.get_device_info()
            if idx == 3:
                raw_brightness = int(di.get("brightness", "50"))
                level = _brightness_raw_to_level(raw_brightness)
                new_level = min(len(BRIGHTNESS_LEVEL_VALUES), level + 1)
                new_brightness = _brightness_level_to_raw(new_level)
                ctx.set_brightness(new_brightness)
            elif idx == 4:
                volume = min(int(di.get("volume", "50")) + 10, 100)
                ctx.set_volume(volume)
        elif buttons.get("btn_up"):
            ctx.setting_select_index = max(3, ctx.setting_select_index - 1)
        elif buttons.get("btn_down"):
            ctx.setting_select_index = min(5, ctx.setting_select_index + 1)
