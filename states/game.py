"""Game states: game select (carousel) and in-game (render from shm)."""
import signal
import time
from PIL import Image, ImageDraw

from domain.app_context import AppContext
from states.base import BaseState
import assets
import runtime.machine_api as machine_api


class GameSelectState(BaseState):
    """Game selection: preview carousel; A start game, B back to menu."""

    def name(self) -> str:
        return "game_select"

    def update(self, ctx: AppContext) -> None:
        if len(ctx.game_list) == 0:
            # Mirror the default empty-page experience by showing the QR code
            # when no games are installed.
            ctx.display.update_frame_buffer(ctx.assets.qrcode_image)
            return
        if time.time() - ctx.page_tick > 5:
            ctx.game_preview_index += 1
            if ctx.game_preview_index >= len(ctx.game_list[ctx.game_index]["preview"]):
                ctx.game_preview_index = 0
            ctx.page_tick = time.time()
        select_img = Image.new("RGB", (128, 160), (0, 0, 0))
        preview_img = Image.frombytes(
            "RGB",
            (128, 128),
            bytes(
                ctx.game_list[ctx.game_index]["preview"][ctx.game_preview_index]
            ),
        )
        select_img.paste(preview_img, (0, 0))
        select_img.paste(ctx.assets.game_select_image, (0, 128))
        ctx.display.update_frame_buffer(select_img)

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        if buttons.get("btn_a"):
            if ctx.term_game_process and ctx.game is not None:
                ctx.term_game_process(ctx.game)
            if ctx.start_game_process and ctx.game_list:
                game = ctx.start_game_process(
                    ctx.game_list[ctx.game_index]["id"]
                )
                if game is not None:
                    if ctx.term_widget_processes and ctx.pages is not None:
                        ctx.term_widget_processes(ctx.pages)
                    ctx.game = game
                    try:
                        if ctx.set_game_status:
                            ctx.set_game_status(str(ctx.game_list[ctx.game_index]["id"]), "playing")
                    except Exception as e:
                        print(f"Warning: Failed to set game status to playing: {e}")
                    ctx.transition_to(InGameState())
                else:
                    ctx.reload_conf = True
        elif buttons.get("btn_b") or buttons.get("btn_home"):
            from states.menu import MenuState
            ctx.transition_to(MenuState())
        elif buttons.get("btn_left"):
            ctx.game_index -= 1
            if ctx.game_index < 0:
                ctx.game_index = len(ctx.game_list) - 1
            ctx.game_preview_index = 0
            ctx.page_tick = time.time()
        elif buttons.get("btn_right"):
            ctx.game_index += 1
            if ctx.game_index >= len(ctx.game_list):
                ctx.game_index = 0
            ctx.game_preview_index = 0
            ctx.page_tick = time.time()


class InGameState(BaseState):
    """In-game: render from game shm; HOME shows pause overlay (A resume, B terminate)."""

    def __init__(self) -> None:
        self._showing_pause_overlay = False
        self._resume_on_a_release = False

    def name(self) -> str:
        return "in_game"

    def is_showing_exit_game_overlay(self, ctx: AppContext) -> bool:
        return self._showing_pause_overlay

    def update(self, ctx: AppContext) -> None:
        game = ctx.game
        if game is None or game == {}:
            ctx.reload_conf = True
            return
        if game["process"].poll() is not None:
            game_id = game.get("game_id")
            try:
                machine_api.stop_game_tracking()
            except Exception as e:
                print(f"Warning: Failed to stop game tracking: {e}")
            try:
                if game_id and ctx.set_game_status:
                    ctx.set_game_status(str(game_id), "ready")
            except Exception as e:
                print(f"Warning: Failed to set game status to ready: {e}")
            ctx.reload_conf = True
            return
        if self._showing_pause_overlay:
            menu_image = Image.new("RGB", (128, 160), (0, 0, 0))
            try:
                game_buf = game["shm"].buf[1:]
                game_image = Image.frombytes(
                    "RGB",
                    (128, 128),
                    bytes(game_buf[: 128 * 128 * 3]),
                )
            except Exception:
                game_image = Image.new("RGB", (128, 128), (0, 0, 0))
            menu_image.paste(game_image, (0, 0))
            overlay = Image.new("RGBA", (128, 128), (0, 0, 0, 128))
            menu_image.paste(overlay, (0, 0), overlay)
            draw = ImageDraw.Draw(menu_image)
            text = "B: End the game"
            text_bbox = draw.textbbox((0, 0), text, font=ctx.assets.font24)
            draw.text(
                ((128 - text_bbox[2]) / 2, (128 - text_bbox[3]) / 2),
                text,
                fill="white",
                font=ctx.assets.font24,
            )
            ctx.display.update_frame_buffer(menu_image)
            return
        game_id = game.get("game_id", "unknown")
        shm_buf0 = game["shm"].buf[0] if game.get("shm") else None
        if game_id == "pico8":
            prev_buf0 = game.get("pico8_prev_buf0", None)
            if shm_buf0 == 0:
                try:
                    game_image = Image.frombytes(
                        "RGB",
                        (128, 160),
                        bytes(game["shm"].buf[1 : 1 + 128 * 160 * 3]),
                    )
                    ctx.display.update_frame_buffer(game_image)
                    game["shm"].buf[0] = 1
                    if prev_buf0 == 1:
                        game["pico8_first_frame_seen"] = True
                    game["pico8_prev_buf0"] = 1
                except Exception as e:
                    print(f"Error rendering pico8 frame: {e}")
                    ctx.display.update_frame_buffer(assets.create_loading_image())
            else:
                game["pico8_prev_buf0"] = 1
                if not game.get("pico8_first_frame_seen", False):
                    ctx.display.update_frame_buffer(assets.create_loading_image())
        else:
            if shm_buf0 == 0:
                game["launched"] = True
                game_image = Image.frombytes(
                    "RGB",
                    (128, 160),
                    bytes(game["shm"].buf[1 : 1 + 128 * 160 * 3]),
                )
                ctx.display.update_frame_buffer(game_image)
                game["shm"].buf[0] = 1
            elif game.get("launched", False):
                ctx.display.update_frame_buffer(assets.create_loading_image())
            else:
                ctx.display.update_frame_buffer(assets.create_loading_image())

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        from states.menu import MenuState
        from states.widget import WidgetState

        if self._showing_pause_overlay:
            if buttons.get("btn_a"):
                self._resume_on_a_release = True
            elif self._resume_on_a_release and not (getattr(ctx, "current_button_state", None) or {}).get("btn_a", False):
                if ctx.game and ctx.game.get("process") and ctx.game["process"].poll() is None:
                    ctx.game["process"].send_signal(signal.SIGCONT)
                self._showing_pause_overlay = False
                self._resume_on_a_release = False
            elif buttons.get("btn_b"):
                game_id = None
                if ctx.game and isinstance(ctx.game, dict):
                    game_id = ctx.game.get("game_id")
                if ctx.term_game_process and ctx.game is not None:
                    ctx.term_game_process(ctx.game)
                ctx.game = None
                try:
                    if game_id and ctx.set_game_status:
                        ctx.set_game_status(str(game_id), "ready")
                except Exception as e:
                    print(f"Warning: Failed to set game status to ready: {e}")
                self._showing_pause_overlay = False
                self._resume_on_a_release = False
                ctx.transition_to(MenuState())
            return
        if buttons.get("btn_b"):
            pass  # B in game: only overlay B ends the game
        elif buttons.get("btn_home"):
            device_info = ctx.get_device_info()
            ctx.trigger_dim_check = True
            if device_info.get("model") == "PixelBoard":
                ctx.reload_conf = True
                ctx.transition_to(WidgetState())
            else:
                if ctx.game and ctx.game.get("process") and ctx.game["process"].poll() is None:
                    ctx.game["process"].send_signal(signal.SIGSTOP)
                self._showing_pause_overlay = True
