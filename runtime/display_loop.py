"""
Main display / input / widget compositing loop (30 FPS).

Dim-window runtime lives in DimWindowRuntime so set_brightness can share state.
"""

from __future__ import annotations

import logging
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, time as dt_time
from typing import Any, Callable, Type

from PIL import Image

from domain.app_context import AppContext

_log = logging.getLogger(__name__)
_UI_STATE_SNAPSHOT_PATH = "/tmp/dartsnut_ui_state.json"
_ui_snapshot_last_write_at = 0.0
_ui_snapshot_last_payload = ""


@dataclass
class DimWindowRuntime:
    currently_in_dim_window: bool = False
    brightness_before_dim: int | None = None
    last_dim_check_time: float = 0.0
    dim_force_normal_brightness: bool = False
    dim_force_normal_start_time: float | None = None


def _build_ui_state_snapshot(ctx: AppContext) -> dict[str, Any]:
    page = None
    pages = ctx.pages or []
    if isinstance(ctx.page_index, int) and 0 <= ctx.page_index < len(pages):
        candidate = pages[ctx.page_index]
        if isinstance(candidate, dict):
            page = candidate

    widget_ids = []
    if isinstance(page, dict):
        for widget in page.get("widgets") or []:
            if not isinstance(widget, dict):
                continue
            widget_data = widget.get("widget") or widget
            if isinstance(widget_data, dict):
                widget_id = str(widget_data.get("id") or "").strip()
                if widget_id:
                    widget_ids.append(widget_id)

    running_game_id = ""
    if isinstance(ctx.game, dict):
        running_game_id = str(ctx.game.get("game_id") or "")

    return {
        "state": str(ctx.state_str or ""),
        "game_id": str(ctx.game_id or ""),
        "running_game_id": running_game_id,
        "page_index": ctx.page_index,
        "page_uuid": str((page or {}).get("uuid") or "") if isinstance(page, dict) else "",
        "page_title": str((page or {}).get("title") or "") if isinstance(page, dict) else "",
        "widget_ids": widget_ids,
        "wifi_connected": bool(ctx.wifi_connected),
        "internet_connected": bool(ctx.internet_connected),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_ui_state_snapshot(ctx: AppContext) -> None:
    global _ui_snapshot_last_payload, _ui_snapshot_last_write_at
    now = time.monotonic()
    if now - _ui_snapshot_last_write_at < 1.0:
        return
    payload = json.dumps(_build_ui_state_snapshot(ctx), sort_keys=True)
    if payload == _ui_snapshot_last_payload:
        _ui_snapshot_last_write_at = now
        return
    tmp_path = f"{_UI_STATE_SNAPSHOT_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        file.write(payload)
    os.replace(tmp_path, _UI_STATE_SNAPSHOT_PATH)
    _ui_snapshot_last_payload = payload
    _ui_snapshot_last_write_at = now


def update_widget_page_framebuffer(page: dict, assets: Any) -> None:
    """Composite new widget frames and per-widget loading indicators into a page."""
    framebuffer = page.get("framebuffer")
    widgets = page.get("widgets")
    if framebuffer is None or not isinstance(widgets, list):
        return

    page_img = Image.frombytes("RGB", (128, 160), bytes(framebuffer))
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
        if not isinstance(position, (list, tuple)) or len(position) != 4:
            continue
        x0, y0, x1, y1 = position
        widget_width = x1 - x0 + 1
        widget_height = y1 - y0 + 1

        process = widget.get("process")
        process_unavailable = process is None
        if process is not None and hasattr(process, "poll"):
            try:
                process_unavailable = process.poll() is not None
            except Exception as e:
                process_unavailable = True
                _log.warning("Error checking widget process for %s: %s", widget_id, e)

        shm = widget.get("shm")
        buf = None
        frame_available = False
        if not process_unavailable and shm is not None:
            try:
                buf = getattr(shm, "buf", None)
                frame_available = buf is not None and buf[0] == 0
            except Exception as e:
                _log.warning("Error accessing widget frame for %s: %s", widget_id, e)

        if frame_available:
            try:
                widget_frame = Image.frombytes(
                    "RGB",
                    (widget_width, widget_height),
                    bytes(buf[1 : 1 + widget_width * widget_height * 3]),
                )
                page_img.paste(widget_frame, (x0, y0))
                first_frame = widget.get("loading", True)
                widget["loading"] = False
                buf[0] = 1
                if widget_height == 160 and first_frame:
                    small_widget_area = widget_frame.crop(
                        (0, 128, widget_width, 160)
                    )
                    widget["has_small_widget"] = any(
                        byte != 0 for byte in small_widget_area.tobytes()
                    )
            except Exception as e:
                _log.warning("Error reading widget frame for %s: %s", widget_id, e)

        is_loading = widget.get("loading")
        if is_loading is None:
            is_loading = True
        if process_unavailable or is_loading:
            if widget_height == 160:
                page_img.paste(current_loading_frame_big, (x0, y0 + 32))
                if widget.get("has_small_widget") is not False:
                    page_img.paste(current_loading_frame, (x0, y0 + 128))
            elif widget_height == 128:
                page_img.paste(current_loading_frame_big, (x0, y0 + 32))
            elif widget_height == 32:
                page_img.paste(current_loading_frame, (x0, y0))

    page["framebuffer"] = bytearray(page_img.tobytes())


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
            try:
                write_ui_state_snapshot(ctx)
            except Exception as e:
                _log.debug("Error writing UI state snapshot: %s", e)

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
                        if ctx.set_game_status:
                            ctx.set_game_status(ctx.game_id, "playing")
                        else:
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
                    if isinstance(page, dict):
                        update_widget_page_framebuffer(page, assets)
        except Exception as e:
            _log.exception("Error in main loop: %s", e)
