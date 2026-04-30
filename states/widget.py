"""Widget state: page rotation, widget processes, framebuffer display."""
import logging
import os
import signal
import time
from PIL import Image

from domain.app_context import AppContext
from states.base import BaseState
from widget_lifecycle import (
    check_page_widget_updates,
    restart_widget_process,
    _kill_widget_process,
    widgets_updated,
)

_log = logging.getLogger(__name__)


class WidgetState(BaseState):
    """Widget mode: show current page framebuffer, handle page rotation and freeze."""

    def name(self) -> str:
        return "widget"

    def update(self, ctx: AppContext) -> None:
        pages = ctx.pages
        if pages is None or len(pages) == 0:
            ctx.reload_conf = True
            return
        if ctx.page_index < 0 or ctx.page_index >= len(pages):
            ctx.page_index = 0

        # If we're currently on the default QR page, jump back immediately to an
        # enabled non-default page when one becomes available.
        current_page = pages[ctx.page_index]
        if current_page.get("uuid") == "0":
            preferred_index = None
            for idx, page in enumerate(pages):
                if page.get("uuid") != "0" and page.get("enabled", True):
                    preferred_index = idx
                    break
            if preferred_index is not None and preferred_index != ctx.page_index:
                ctx.page_index = preferred_index
                ctx.page_tick = time.time()
                ctx.next_page_prepared_index = -1
                _log.info(
                    "widget: left default QR page, active_page_index=%s", ctx.page_index
                )

        get_context = lambda: ctx

        if len(pages) > 1 and not ctx.page_freeze:
            next_index = ctx.page_index
            found_enabled = False
            for _ in range(len(pages)):
                next_index = (next_index + 1) % len(pages)
                if pages[next_index].get("enabled", True) and pages[next_index]["uuid"] != "0":
                    found_enabled = True
                    break
            if not found_enabled:
                next_index = len(pages) - 1
            if (time.time() - ctx.page_tick > int(pages[ctx.page_index]["duration"]) - 3) and (
                ctx.next_page_prepared_index != next_index
            ):
                for widget in pages[next_index]["widgets"]:
                    try:
                        process = widget.get("process")
                        if process is None:
                            widget["launched"] = False
                            continue
                        os.kill(process.pid, signal.SIGCONT)
                        widget["launched"] = False
                    except Exception as e:
                        _log.warning("Error resuming next widget process: %s", e)
                ctx.next_page_prepared_index = next_index
            if (time.time() - ctx.page_tick > int(pages[ctx.page_index]["duration"])) or (
                not pages[ctx.page_index]["enabled"]
            ):
                ctx.page_index = next_index
                ctx.page_tick = time.time()
                ctx.next_page_prepared_index = -1
                _log.debug("widget: rotated to page_index=%s", ctx.page_index)

        if ctx.page_index != ctx.last_page_index:
            for i in range(len(pages)):
                if i == ctx.page_index:
                    for widget_idx, widget_entry in enumerate(pages[i]["widgets"]):
                        try:
                            process = widget_entry.get("process")
                            if process is None:
                                widget = widget_entry.get("widget")
                                if widget and widget.get("id") != "0":
                                    wid = widget.get("id")
                                    _log.info("widget: restarting %s (process was killed)", wid)
                                    restart_widget_process(widget_entry, pages[i], widget_idx)
                                continue
                            if process.poll() is None:
                                os.kill(process.pid, signal.SIGCONT)
                                widget_entry["launched"] = False
                            else:
                                widget = widget_entry.get("widget")
                                if widget and widget.get("id") != "0":
                                    wid = widget.get("id")
                                    _log.info("widget: restarting %s (process was killed)", wid)
                                    restart_widget_process(widget_entry, pages[i], widget_idx)
                        except Exception as e:
                            _log.warning("Error resuming widget process: %s", e)
                else:
                    for widget_entry in pages[i]["widgets"]:
                        try:
                            widget = widget_entry.get("widget")
                            widget_id = widget.get("id") if widget else None
                            if widget_id and widget_id in widgets_updated:
                                _kill_widget_process(
                                    widget_entry, widget_id, "suspending page, update complete"
                                )
                                widgets_updated.discard(widget_id)
                            else:
                                process = widget_entry.get("process")
                                if process and process.poll() is None:
                                    os.kill(process.pid, signal.SIGSTOP)
                                    widget_entry["launched"] = False
                        except Exception as e:
                            _log.warning("Error pausing widget process: %s", e)
            ctx.last_page_index = ctx.page_index
            check_page_widget_updates(pages[ctx.page_index], get_context)
        else:
            for widget_idx, widget_entry in enumerate(pages[ctx.page_index]["widgets"]):
                try:
                    process = widget_entry.get("process")
                    if process is None:
                        widget = widget_entry.get("widget")
                        if widget and widget.get("id") != "0":
                            wid = widget.get("id")
                            _log.info("widget: restarting %s (process was killed)", wid)
                            restart_widget_process(
                                widget_entry, pages[ctx.page_index], widget_idx
                            )
                    elif process.poll() is not None:
                        widget = widget_entry.get("widget")
                        if widget and widget.get("id") != "0":
                            wid = widget.get("id")
                            _log.info("widget: restarting %s (process died)", wid)
                            restart_widget_process(
                                widget_entry, pages[ctx.page_index], widget_idx
                            )
                except Exception as e:
                    _log.warning("Error checking widget process: %s", e)

        widget_img = Image.frombytes(
            "RGB", (128, 160), bytes(pages[ctx.page_index]["framebuffer"])
        )
        assets = ctx.assets
        if ctx.page_freeze:
            widget_img.paste(
                assets.lock_widget_icon, (117, 117), assets.lock_widget_icon.convert("RGBA")
            )
        if not ctx.wifi_connected:
            if (time.time() % 2) < 1:
                widget_img.paste(
                    assets.wifi_disconnect_icon,
                    (117, 0),
                    assets.wifi_disconnect_icon.convert("RGBA"),
                )
        elif not ctx.internet_connected:
            if (time.time() % 2) < 1:
                widget_img.paste(
                    assets.internet_disconnect_icon,
                    (117, 0),
                    assets.internet_disconnect_icon.convert("RGBA"),
                )
        ctx.display.update_frame_buffer(widget_img)

    def handle_input(self, ctx: AppContext, buttons: dict) -> None:
        if buttons.get("btn_a"):
            ctx.page_freeze = not ctx.page_freeze
            ctx.page_tick = time.time()
        elif buttons.get("btn_left"):
            pages = ctx.pages
            if pages:
                prev_index = ctx.page_index
                found = False
                for _ in range(len(pages)):
                    prev_index = (prev_index - 1 + len(pages)) % len(pages)
                    if pages[prev_index].get("enabled", True) and pages[prev_index]["uuid"] != "0":
                        ctx.page_index = prev_index
                        found = True
                        break
                if not found:
                    ctx.page_index = len(pages) - 1
                ctx.page_tick = time.time()
        elif buttons.get("btn_right"):
            pages = ctx.pages
            if pages:
                next_index = ctx.page_index
                found = False
                for _ in range(len(pages)):
                    next_index = (next_index + 1) % len(pages)
                    if pages[next_index].get("enabled", True) and pages[next_index]["uuid"] != "0":
                        ctx.page_index = next_index
                        found = True
                        break
                if not found:
                    ctx.page_index = len(pages) - 1
                ctx.page_tick = time.time()
        elif buttons.get("btn_home"):
            device_info = ctx.get_device_info()
            if device_info.get("model") == "PixelBoard":
                ctx.page_freeze = not ctx.page_freeze
                ctx.page_tick = time.time()
            else:
                from states.menu import MenuState
                ctx.transition_to(MenuState())
