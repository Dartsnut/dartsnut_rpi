"""Settings state: brightness, volume, IP, version."""
import subprocess
from PIL import Image, ImageDraw

from app_context import AppContext
from states.base import BaseState


SETTINGS_ITEMS = [
    {"name": "Brightness", "type": "value"},
    {"name": "Volume", "type": "value"},
    {"name": "IP", "type": "info"},
    {"name": "Version", "type": "info"},
]


class SettingsState(BaseState):
    """Settings menu: brightness, volume, IP, version; B/home back to menu."""

    def name(self) -> str:
        return "settings"

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

        item_height = 32
        font8 = ctx.assets.font8
        for idx, item in enumerate(SETTINGS_ITEMS):
            y = idx * item_height
            focused = idx == ctx.setting_select_index
            if focused:
                draw.rectangle(
                    (0, y, 127, y + item_height - 1), fill=(40, 40, 40)
                )
            draw.text(
                (2, y + 12), item["name"].upper(), fill="white", font=font8
            )
            if item["name"] == "Brightness":
                value_str = f"{brightness}"
                value_x = 80
                arrow_left_x = value_x - 13
                arrow_right_x = value_x + 23
                if focused:
                    draw.text((arrow_left_x, y + 12), "<", fill="white", font=font8)
                    draw.text((arrow_right_x, y + 12), ">", fill="white", font=font8)
                draw.text((value_x, y + 12), value_str, fill="white", font=font8)
            elif item["name"] == "Volume":
                value_str = f"{volume}"
                value_x = 80
                arrow_left_x = value_x - 13
                arrow_right_x = value_x + 23
                if focused:
                    draw.text((arrow_left_x, y + 12), "<", fill="white", font=font8)
                    draw.text((arrow_right_x, y + 12), ">", fill="white", font=font8)
                draw.text((value_x, y + 12), value_str, fill="white", font=font8)
            elif item["name"] == "IP":
                text_width = len(ip_address) * 6
                draw.text(
                    (48 + (80 - text_width) / 2, y + 12),
                    ip_address,
                    fill="white",
                    font=font8,
                )
            elif item["name"] == "Version":
                text_width = len(version) * 6
                draw.text(
                    (48 + (80 - text_width) / 2, y + 12),
                    version,
                    fill="white",
                    font=font8,
                )
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
        ctx.display.update_frame_buffer(settings_image)

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        if buttons.get("btn_b") or buttons.get("btn_home"):
            from states.menu import MenuState
            ctx.transition_to(MenuState())
        elif buttons.get("btn_left"):
            idx = ctx.setting_select_index
            di = ctx.get_device_info()
            if idx == 0:
                brightness = max(int(di.get("brightness", "50")) - 10, 10)
                ctx.set_brightness(brightness)
            elif idx == 1:
                volume = max(int(di.get("volume", "50")) - 10, 0)
                ctx.set_volume(volume)
        elif buttons.get("btn_right"):
            idx = ctx.setting_select_index
            di = ctx.get_device_info()
            if idx == 0:
                brightness = min(int(di.get("brightness", "50")) + 10, 100)
                ctx.set_brightness(brightness)
            elif idx == 1:
                volume = min(int(di.get("volume", "50")) + 10, 100)
                ctx.set_volume(volume)
        elif buttons.get("btn_up"):
            ctx.setting_select_index = max(0, ctx.setting_select_index - 1)
        elif buttons.get("btn_down"):
            ctx.setting_select_index = min(1, ctx.setting_select_index + 1)
