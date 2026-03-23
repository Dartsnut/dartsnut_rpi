"""Menu state: main menu with games/widgets/settings icons and selection."""
import os
import time
from PIL import Image, ImageDraw

from app_context import AppContext
from states.base import BaseState


def _check_firmware_updated_flag() -> bool:
    flag_path = "/tmp/firmware_updated.flag"
    return os.path.isfile(flag_path)


def _remove_firmware_updated_flag() -> None:
    flag_path = "/tmp/firmware_updated.flag"
    try:
        if os.path.isfile(flag_path):
            os.remove(flag_path)
    except Exception:
        pass


def _draw_firmware_updated_text(draw: ImageDraw.Draw, x: int, y: int, font8) -> None:
    firmware_text1 = "FIRMWARE"
    firmware_text2 = "UPDATED"
    gap_width = 6
    firmware_text1_width = len(firmware_text1) * 6
    firmware_text2_width = len(firmware_text2) * 6
    total_width = firmware_text1_width + gap_width + firmware_text2_width
    if x < 0:
        firmware_text_x = int((128 - total_width) / 2)
    else:
        firmware_text_x = x
    draw.text((firmware_text_x, y), firmware_text1, fill=(255, 255, 255), font=font8)
    draw.text(
        (firmware_text_x + firmware_text1_width + gap_width, y),
        firmware_text2,
        fill=(255, 255, 255),
        font=font8,
    )


class MenuState(BaseState):
    """Main menu: logo, icons, selection; transitions to widget/game_select/settings."""

    def name(self) -> str:
        return "menu"

    def update(self, ctx: AppContext) -> None:
        device_info = ctx.get_device_info()
        if device_info.get("model") == "PixelBoard":
            ctx.reload_conf = True
            ctx.pending_state = "widget"
            return
        # PixelDart: draw menu
        assets = ctx.assets
        menu_image = Image.new("RGB", (128, 160), (0, 0, 0))
        menu_image.paste(assets.logo_image, (0, 0))
        menu_image.paste(assets.game_icon, (4, 132), assets.game_icon.convert("RGBA"))
        menu_image.paste(assets.widget_icon, (24, 132), assets.widget_icon.convert("RGBA"))
        menu_image.paste(
            assets.settings_icon, (44, 132), assets.settings_icon.convert("RGBA")
        )
        draw = ImageDraw.Draw(menu_image)
        idx = ctx.menu_select_index
        draw.rounded_rectangle(
            (idx * 20 + 2, 130, idx * 20 + 21, 149),
            radius=4,
            outline="white",
            width=1,
        )
        if idx == 0:
            text = "GAMES"
        elif idx == 1:
            text = "WIDGETS"
        else:
            text = "SETTINGS"
        text_width = len(text) * 6
        text_x = int((64 - text_width) / 2)
        draw.text((text_x, 152), text, fill=(255, 255, 255), font=assets.font8)
        if _check_firmware_updated_flag():
            _draw_firmware_updated_text(draw, -1, 120, assets.font8)
        ctx.display.update_frame_buffer(menu_image)

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        from states.widget import WidgetState
        from states.game import GameSelectState
        from states.settings import SettingsState

        if buttons.get("btn_a"):
            if _check_firmware_updated_flag():
                _remove_firmware_updated_flag()
            idx = ctx.menu_select_index
            if idx == 0:
                if ctx.load_game_list:
                    ctx.game_list = ctx.load_game_list()
                ctx.game_index = 0
                ctx.game_preview_index = 0
                ctx.page_tick = time.time()
                ctx.transition_to(GameSelectState())
            elif idx == 1:
                ctx.transition_to(WidgetState())
            elif idx == 2:
                ctx.transition_to(SettingsState())
        elif buttons.get("btn_b"):
            if ctx.game is not None and ctx.term_game_process:
                ctx.term_game_process(ctx.game)
                ctx.game = None
        elif buttons.get("btn_left"):
            ctx.menu_select_index -= 1
            if ctx.menu_select_index < 0:
                ctx.menu_select_index = 2
        elif buttons.get("btn_right"):
            ctx.menu_select_index += 1
            if ctx.menu_select_index > 2:
                ctx.menu_select_index = 0
        elif buttons.get("btn_home"):
            device_info = ctx.get_device_info()
            if device_info.get("model") == "PixelBoard":
                pass  # toggle freeze handled in widget state
            else:
                # In menu: go to widget if we have pages, else no-op
                if ctx.pages is not None and len(ctx.pages) > 0:
                    ctx.transition_to(WidgetState())
