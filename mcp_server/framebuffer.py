from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from typing import Iterable

from PIL import Image

FRAMEBUFFER_WIDTH = 128
FRAMEBUFFER_HEIGHT = 160
FRAMEBUFFER_BYTES = FRAMEBUFFER_WIDTH * FRAMEBUFFER_HEIGHT * 3

DEFAULT_FRAMEBUFFER_PATHS = (
    "/dev/shm/pdoshm",
    "/dev/shm/pdishm",
)


@dataclass(frozen=True)
class CapturedScreen:
    surface: str
    width: int
    height: int
    mime_type: str
    data: str


def _candidate_paths() -> Iterable[str]:
    override = os.getenv("DARTSNUT_MCP_FRAMEBUFFER_PATH", "").strip()
    if override:
        yield override
    yield from DEFAULT_FRAMEBUFFER_PATHS


def _read_framebuffer_bytes() -> bytes:
    for path in _candidate_paths():
        try:
            with open(path, "rb") as file:
                data = file.read()
        except OSError:
            continue
        if len(data) >= FRAMEBUFFER_BYTES:
            return data[:FRAMEBUFFER_BYTES]
    raise FileNotFoundError("No readable 128x160 RGB framebuffer found in /dev/shm")


def capture_screen(surface: str = "full", image_format: str = "png") -> CapturedScreen:
    normalized_surface = str(surface or "full").lower()
    normalized_format = str(image_format or "png").lower()
    if normalized_format != "png":
        raise ValueError("Only png format is supported")

    image = Image.frombytes(
        "RGB",
        (FRAMEBUFFER_WIDTH, FRAMEBUFFER_HEIGHT),
        _read_framebuffer_bytes(),
    )

    if normalized_surface == "full":
        cropped = image
    elif normalized_surface == "top":
        cropped = image.crop((0, 0, 128, 128))
    elif normalized_surface == "bottom":
        cropped = image.crop((0, 128, 64, 160))
    else:
        raise ValueError("surface must be one of: full, top, bottom")

    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return CapturedScreen(
        surface=normalized_surface,
        width=cropped.width,
        height=cropped.height,
        mime_type="image/png",
        data=base64.b64encode(buffer.getvalue()).decode("ascii"),
    )

