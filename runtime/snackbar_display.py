"""Thread-safe framebuffer compositor for transient main-surface snackbars."""

from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 160
MAIN_SURFACE_HEIGHT = 128
PDM_HEIGHT = DISPLAY_HEIGHT - MAIN_SURFACE_HEIGHT
SNACKBAR_HEIGHT = 12
SNACKBAR_DURATION_SECONDS = 5.0
SNACKBAR_ANIMATION_SECONDS = 0.2
CONTROLLER_CONNECTED_MESSAGE = "CONTROLLER CONNECTED"
FIRMWARE_UPDATING_MESSAGE = "FIRMWARE UPDATING"


class SnackbarDisplay:
    """Cache full frames and composite a snackbar without touching PDM rows."""

    def __init__(
        self,
        display: Any,
        *,
        font: Any | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._display = display
        self._font = font or ImageFont.load_default()
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._base_frame = Image.new(
            "RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (0, 0, 0)
        )
        self._message: str | None = None
        self._shown_at = 0.0
        self._persistent = False
        self._dismissed_at: float | None = None
        self._dismiss_start_progress = 0.0
        self._snackbar_token = 0
        self._hide_base_frame: Image.Image | None = None
        self._dirty = False
        self._last_presented_bytes: bytes | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._display, name)

    def update_frame_buffer(self, frame: Any) -> bool:
        """Cache a 128x128 or 128x160 frame and attempt to present it."""
        image = self._coerce_frame(frame)
        with self._lock:
            if image.size == (DISPLAY_WIDTH, MAIN_SURFACE_HEIGHT):
                self._base_frame.paste(image, (0, 0))
            elif image.size == (DISPLAY_WIDTH, DISPLAY_HEIGHT):
                self._base_frame = image.copy()
            else:
                raise ValueError(
                    "frame must be 128x128 or 128x160 RGB image data"
                )
            self._dirty = True
            return self._present_locked(self._monotonic())

    def show_snackbar(self, message: str, *, persistent: bool = False) -> int | None:
        """Show or replace a snackbar and return its dismissal token."""
        normalized = str(message or "").strip().upper()
        if not normalized:
            return None
        with self._lock:
            self._snackbar_token += 1
            self._message = normalized
            self._shown_at = self._monotonic()
            self._persistent = bool(persistent)
            self._dismissed_at = None
            self._dismiss_start_progress = 0.0
            self._hide_base_frame = None
            self._dirty = True
            return self._snackbar_token

    def dismiss_snackbar(self, token: int | None = None) -> bool:
        """Slide out the active snackbar, optionally only if its token matches."""
        with self._lock:
            if self._message is None:
                return False
            if token is not None and token != self._snackbar_token:
                return False
            now = self._monotonic()
            self._dismiss_start_progress = self._active_progress(now)
            self._dismissed_at = now
            self._dirty = True
            return True

    def present(self) -> bool:
        """Advance snackbar animation and retry a previously busy display."""
        with self._lock:
            return self._present_locked(self._monotonic())

    def _present_locked(self, now: float) -> bool:
        progress = self._snackbar_progress(now)
        frame = self._base_frame.copy()
        if self._message is not None and progress > 0:
            self._draw_snackbar(frame, self._message, progress)
        elif self._hide_base_frame is not None:
            frame = self._hide_base_frame.copy()

        frame_bytes = frame.tobytes()
        if not self._dirty and frame_bytes == self._last_presented_bytes:
            return True

        result = self._display.update_frame_buffer(frame)
        success = result is not False
        if success:
            self._last_presented_bytes = frame_bytes
            self._dirty = False
            if self._message is None and self._hide_base_frame is not None:
                self._hide_base_frame = None
        else:
            self._dirty = True
        return success

    def _snackbar_progress(self, now: float) -> float:
        if self._message is None:
            return 0.0

        if self._dismissed_at is not None:
            dismiss_elapsed = max(0.0, now - self._dismissed_at)
            if dismiss_elapsed < SNACKBAR_ANIMATION_SECONDS:
                return self._dismiss_start_progress * (
                    1.0 - dismiss_elapsed / SNACKBAR_ANIMATION_SECONDS
                )
            self._clear_snackbar()
            return 0.0

        progress = self._active_progress(now)
        if self._persistent:
            return progress

        elapsed = max(0.0, now - self._shown_at)
        hold_end = SNACKBAR_DURATION_SECONDS - SNACKBAR_ANIMATION_SECONDS
        if elapsed < hold_end:
            return progress
        if elapsed < SNACKBAR_DURATION_SECONDS:
            return 1.0 - (
                (elapsed - hold_end) / SNACKBAR_ANIMATION_SECONDS
            )

        self._clear_snackbar()
        return 0.0

    def _active_progress(self, now: float) -> float:
        elapsed = max(0.0, now - self._shown_at)
        return min(1.0, elapsed / SNACKBAR_ANIMATION_SECONDS)

    def _clear_snackbar(self) -> None:
        self._message = None
        self._persistent = False
        self._dismissed_at = None
        self._dismiss_start_progress = 0.0
        self._hide_base_frame = self._base_frame.copy()
        self._dirty = True

    def _draw_snackbar(self, frame: Image.Image, message: str, progress: float) -> None:
        visible_height = max(
            0, min(SNACKBAR_HEIGHT, round(SNACKBAR_HEIGHT * progress))
        )
        if visible_height <= 0:
            return

        snackbar = Image.new(
            "RGB", (DISPLAY_WIDTH, SNACKBAR_HEIGHT), (255, 255, 255)
        )
        draw = ImageDraw.Draw(snackbar)
        bbox = draw.textbbox((0, 0), message, font=self._font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        text_x = max(0, (DISPLAY_WIDTH - text_width) // 2 - bbox[0])
        text_y = max(0, (SNACKBAR_HEIGHT - text_height) // 2 - bbox[1])
        draw.text((text_x, text_y), message, fill=(0, 0, 0), font=self._font)

        destination_y = MAIN_SURFACE_HEIGHT - visible_height
        source_y = SNACKBAR_HEIGHT - visible_height
        frame.paste(
            snackbar.crop((0, source_y, DISPLAY_WIDTH, SNACKBAR_HEIGHT)),
            (0, destination_y),
        )

    @staticmethod
    def _coerce_frame(frame: Any) -> Image.Image:
        if isinstance(frame, Image.Image):
            return frame.convert("RGB") if frame.mode != "RGB" else frame.copy()

        if isinstance(frame, (bytes, bytearray, memoryview)):
            data = bytes(frame)
            if len(data) == DISPLAY_WIDTH * MAIN_SURFACE_HEIGHT * 3:
                size = (DISPLAY_WIDTH, MAIN_SURFACE_HEIGHT)
            elif len(data) == DISPLAY_WIDTH * DISPLAY_HEIGHT * 3:
                size = (DISPLAY_WIDTH, DISPLAY_HEIGHT)
            else:
                raise ValueError(
                    "frame byte data must contain a 128x128 or 128x160 RGB frame"
                )
            return Image.frombytes("RGB", size, data)

        raise TypeError("frame must be an image or RGB byte buffer")


def wrap_firmware_update_with_snackbar(
    display: SnackbarDisplay,
    perform_update: Callable[..., Any],
) -> Callable[..., Any]:
    """Return an update callable that announces firmware work before blocking."""

    @wraps(perform_update)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        token = display.show_snackbar(
            FIRMWARE_UPDATING_MESSAGE, persistent=True
        )
        try:
            return perform_update(*args, **kwargs)
        finally:
            display.dismiss_snackbar(token)

    return wrapped
