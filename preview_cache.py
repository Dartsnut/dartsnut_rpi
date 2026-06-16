"""Disk cache for game preview images fetched from community API."""
import io
import json
import logging
import os
import tempfile
import time
from typing import List, Optional

from PIL import Image

_log = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = os.path.expanduser("~/.dartsnut/preview_cache")


class PreviewCache:
    """Manages a directory of cached game preview images with metadata."""

    def __init__(self, cache_dir: str = _DEFAULT_CACHE_DIR):
        self._dir = cache_dir
        os.makedirs(self._dir, exist_ok=True)

    def _validate_game_id(self, game_id: str) -> None:
        """Raise ValueError if game_id is unsafe for use as a filename component."""
        if "/" in game_id or "\\" in game_id or game_id.startswith("."):
            raise ValueError(f"Invalid game_id: {game_id!r}")

    def _img_path(self, game_id: str) -> str:
        return os.path.join(self._dir, f"{game_id}.png")

    def _meta_path(self, game_id: str) -> str:
        return os.path.join(self._dir, f"{game_id}.meta.json")

    def get_cached_preview(self, game_id: str) -> Optional[List[bytearray]]:
        """Return cached preview as [bytearray(128x160 RGB)] or None on miss.

        The image is resized to 128x128 (intentional letterboxing): this matches
        game_lifecycle.py which resizes to 128x128 then pastes onto a 128x160
        black canvas, producing a 32-pixel black band at the bottom. The black
        band is correct and expected — do not remove the 128x128 resize.
        """
        self._validate_game_id(game_id)
        img_path = self._img_path(game_id)
        if not os.path.isfile(img_path):
            return None
        try:
            img = Image.open(img_path).convert("RGB").resize((128, 128), Image.LANCZOS)
            canvas = Image.new("RGB", (128, 160), (0, 0, 0))
            canvas.paste(img, (0, 0))
            return [bytearray(canvas.tobytes())]
        except Exception as e:
            _log.warning("[Preview] Failed to read cached image for %s: %s", game_id, e)
            return None

    def save_cached_preview(
        self,
        game_id: str,
        image_data: bytes,
        etag: str,
        last_modified: str,
    ) -> None:
        """Save raw image bytes and HTTP metadata to cache."""
        self._validate_game_id(game_id)
        img_path = self._img_path(game_id)
        meta_path = self._meta_path(game_id)
        try:
            # Validate image before saving
            img = Image.open(io.BytesIO(image_data))
            img.verify()
            # Re-open after verify (verify closes stream)
            img = Image.open(io.BytesIO(image_data))
            img.save(img_path, format="PNG")
        except Exception as e:
            _log.error("[Preview] Failed to save image for %s: %s", game_id, e)
            raise
        meta = {
            "etag": etag,
            "last_modified": last_modified,
            "fetch_time": time.time(),
        }
        with tempfile.NamedTemporaryFile("w", dir=self._dir, delete=False, suffix=".tmp") as tf:
            json.dump(meta, tf)
            tf.flush()
        os.replace(tf.name, meta_path)

    def get_cache_metadata(self, game_id: str) -> Optional[dict]:
        """Return cached metadata dict or None if missing."""
        self._validate_game_id(game_id)
        meta_path = self._meta_path(game_id)
        if not os.path.isfile(meta_path):
            return None
        try:
            with open(meta_path) as f:
                return json.load(f)
        except Exception as e:
            _log.warning("[Preview] Failed to read cache metadata for %s: %s", game_id, e)
            return None

    def is_cache_expired(self, game_id: str, max_age_hours: float = 24.0) -> bool:
        """Return True if cache is missing or older than max_age_hours."""
        self._validate_game_id(game_id)
        meta = self.get_cache_metadata(game_id)
        if meta is None:
            return True
        age_seconds = time.time() - meta.get("fetch_time", 0)
        return age_seconds > max_age_hours * 3600

    def update_fetch_time(self, game_id: str) -> None:
        """Update the fetch_time in metadata to now (after successful revalidation)."""
        self._validate_game_id(game_id)
        meta_path = self._meta_path(game_id)
        meta = self.get_cache_metadata(game_id)
        if meta is None:
            _log.warning("[Preview] update_fetch_time: no metadata found for %s", game_id)
            return
        meta["fetch_time"] = time.time()
        with tempfile.NamedTemporaryFile("w", dir=self._dir, delete=False, suffix=".tmp") as tf:
            json.dump(meta, tf)
            tf.flush()
        os.replace(tf.name, meta_path)
