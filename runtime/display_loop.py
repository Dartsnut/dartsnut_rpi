"""
Main display / input / widget compositing loop (30 FPS).

Dim-window runtime lives in DimWindowRuntime so set_brightness can share state.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Any, Callable, Type

from PIL import Image

from domain.app_context import AppContext

_log = logging.getLogger(__name__)


@dataclass
class DimWindowRuntime:
    currently_in_dim_window: bool = False
    brightness_before_dim: int | None = None
    last_dim_check_time: float = 0.0
    dim_force_normal_brightness: bool = False
    dim_force_normal_start_time: float | None = None


def run_main_loop(
    *,
    dim_rt: DimWindowRuntime,
    dartsnut: Any,
    ctx: AppContext,
    assets: Any,
    get_device_info: Callable[[], dict],
    parse_hhmm: Callable[[str], Any],
    update_brightness_transition: Callable[[], None],
    start_brightness_transition: Callable[[int], None],
    init_widgets: Callable[[AppContext], None],
    reload_pages_from_conf: Callable[[AppContext], None],
    term_game_process: Callable[..., Any],
    start_game_process: Callable[..., Any],
    term_widget_processes: Callable[..., None],
    get_buttons_pressed: Callable[[AppContext], dict],
    check_widget_ready: Callable[..., bool],
    in_game_state_cls: Type[Any],
    get_remote_sync: Callable[[], Any],
) -> None:
    while dartsnut.running:
        try:
            time.sleep(1 / 30)
            assets.get_current_loading_frame()

            update_brightness_transition()

            if ctx.trigger_dim_check:
                ctx.trigger_dim_check = False
                dim_rt.last_dim_check_time = 0

            if (
                time.time() - dim_rt.last_dim_check_time >= 60
                or dim_rt.last_dim_check_time == 0
            ):
                dim_rt.last_dim_check_time = time.time()
                was_dim_window = dim_rt.currently_in_dim_window
                di = get_device_info()
                enabled = str(di.get("dim_window_enabled", "false")).lower() == "true"
                start_s = (di.get("dim_window_start") or "").strip()
                end_s = (di.get("dim_window_end") or "").strip()
                dim_lvl = int(di.get("dim_level", 10))
                if not enabled or not start_s or not end_s:
                    if dim_rt.currently_in_dim_window:
                        restore = (
                            dim_rt.brightness_before_dim
                            if dim_rt.brightness_before_dim is not None
                            else int(di.get("brightness", 50))
                        )
                        start_brightness_transition(restore)
                        dim_rt.currently_in_dim_window = False
                        dim_rt.dim_force_normal_brightness = False
                        dim_rt.dim_force_normal_start_time = None
                else:
                    start_hm = parse_hhmm(start_s)
                    end_hm = parse_hhmm(end_s)
                    if start_hm is None or end_hm is None:
                        if dim_rt.currently_in_dim_window:
                            restore = (
                                dim_rt.brightness_before_dim
                                if dim_rt.brightness_before_dim is not None
                                else int(di.get("brightness", 50))
                            )
                            start_brightness_transition(restore)
                            dim_rt.currently_in_dim_window = False
                            dim_rt.dim_force_normal_brightness = False
                            dim_rt.dim_force_normal_start_time = None
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
                                and not dim_rt.dim_force_normal_brightness
                            ):
                                if not dim_rt.currently_in_dim_window:
                                    dim_rt.brightness_before_dim = int(
                                        di.get("brightness", 50)
                                    )
                                start_brightness_transition(dim_lvl)
                                dim_rt.currently_in_dim_window = True
                        else:
                            if dim_rt.currently_in_dim_window:
                                restore = (
                                    dim_rt.brightness_before_dim
                                    if dim_rt.brightness_before_dim is not None
                                    else int(di.get("brightness", 50))
                                )
                                start_brightness_transition(restore)
                                dim_rt.currently_in_dim_window = False
                                dim_rt.dim_force_normal_brightness = False
                                dim_rt.dim_force_normal_start_time = None

                if dim_rt.currently_in_dim_window != was_dim_window:
                    _log.info(
                        "dim window %s",
                        "active (brightness reduced)" if dim_rt.currently_in_dim_window else "inactive (restored)",
                    )

            ctx.state_str = ctx.current_state.name()

            if ctx.locate_device_intv:
                dartsnut.update_frame_buffer(assets.identify_image)
                ctx.locate_device_intv -= 1
            elif ctx.reload_conf:
                ctx.reload_conf = False
                _log.info("reload_conf: hard widget re-init (init_widgets)")
                init_widgets(ctx)
            elif getattr(ctx, "reload_pages", False):
                ctx.reload_pages = False
                _log.info("reload_pages: soft conf.json reload")
                reload_pages_from_conf(ctx)
            elif ctx.start_game:
                ctx.start_game = False
                term_game_process(ctx.game)
                ctx.game = start_game_process(ctx.game_id)
                if ctx.game is not None:
                    term_widget_processes(ctx.pages)
                    ctx.transition_to(in_game_state_cls())
                    try:
                        get_remote_sync().request_set_game_status(
                            ctx.game_id, "playing"
                        )
                    except Exception as e:
                        _log.warning("Error updating remote game status to playing: %s", e)
            else:
                ctx.current_state.update(ctx)

            buttons = get_buttons_pressed(ctx)
            ctx.current_button_state = dict(get_buttons_pressed.old_buttons)

            if (
                ctx.current_state.name() != "in_game"
                and ctx.current_state.name() != "game_select"
                and not ctx.current_state.is_showing_exit_game_overlay(ctx)
                and dim_rt.currently_in_dim_window
            ):
                di = get_device_info()
                dim_lvl = int(di.get("dim_level", 10))
                if (
                    dim_rt.dim_force_normal_brightness
                    and dim_rt.dim_force_normal_start_time is not None
                ):
                    secs = max(5, min(300, int(di.get("dim_restore_seconds", 30))))
                    if time.time() - dim_rt.dim_force_normal_start_time >= secs:
                        dim_rt.dim_force_normal_brightness = False
                        dim_rt.dim_force_normal_start_time = None
                        start_brightness_transition(dim_lvl)
                if dim_rt.dim_force_normal_brightness and buttons.get("btn_b"):
                    btn_b_handled_by_state = (
                        ctx.current_state.is_showing_exit_game_overlay(ctx)
                        or ctx.current_state.name() == "game_select"
                        or ctx.current_state.consumes_btn_b_for_overlay(ctx)
                    )
                    if not btn_b_handled_by_state:
                        dim_rt.dim_force_normal_brightness = False
                        dim_rt.dim_force_normal_start_time = None
                        start_brightness_transition(dim_lvl)
                        buttons["btn_b"] = False
                elif not dim_rt.dim_force_normal_brightness and buttons.get("btn_a"):
                    dim_rt.dim_force_normal_brightness = True
                    dim_rt.dim_force_normal_start_time = time.time()
                    restore = (
                        dim_rt.brightness_before_dim
                        if dim_rt.brightness_before_dim is not None
                        else int(di.get("brightness", 50))
                    )
                    start_brightness_transition(restore)
                    buttons["btn_a"] = False

            ctx.current_state.handle_input(ctx, buttons)

            if ctx.pages is not None and len(ctx.pages) > 0:
                for page in ctx.pages:
                    if not isinstance(page, dict):
                        continue

                    framebuffer = page.get("framebuffer")
                    if framebuffer is None:
                        continue

                    widgets = page.get("widgets")
                    if not isinstance(widgets, list):
                        continue

                    page_img = Image.frombytes("RGB", (128, 160), bytes(framebuffer))
                    current_loading_frame_big = assets.get_current_loading_frame_big()
                    current_loading_frame = assets.get_current_loading_frame()
                    if current_loading_frame_big.mode != "RGB":
                        current_loading_frame_big = current_loading_frame_big.convert(
                            "RGB"
                        )
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
                                _log.warning("Error reading widget frame for %s: %s", widget_id, e)

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
                                page_img.paste(
                                    current_loading_frame_big, (x0, y0 + 32)
                                )
                                has_small_widget = widget.get("has_small_widget", None)
                                if has_small_widget is not False:
                                    page_img.paste(
                                        current_loading_frame, (x0, y0 + 128)
                                    )
                            elif widget_height == 128:
                                page_img.paste(
                                    current_loading_frame_big, (x0, y0 + 32)
                                )
                            elif widget_height == 32:
                                page_img.paste(current_loading_frame, (x0, y0))

                    page["framebuffer"] = bytearray(page_img.tobytes())
        except Exception as e:
            _log.exception("Error in main loop: %s", e)
