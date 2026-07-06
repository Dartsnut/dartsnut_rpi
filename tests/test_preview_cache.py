"""Tests for preview_cache module."""
import io
import json
import os
import time
import pytest
from unittest.mock import patch
from PIL import Image


def _make_png_bytes():
    """Create a minimal valid PNG image as bytes."""
    img = Image.new("RGB", (128, 128), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path / "preview_cache")


def test_save_and_get_cached_preview(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    game_id = "test_game"
    image_bytes = _make_png_bytes()

    pc.save_cached_preview(game_id, image_bytes, etag='"abc"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    result = pc.get_cached_preview(game_id)

    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], bytearray)
    assert len(result[0]) == 128 * 160 * 3  # RGB, 128x160

    # Verify letterboxing: bottom 32 rows (rows 128-159) must be all black zeros
    letterbox_start = 128 * 128 * 3
    assert all(b == 0 for b in result[0][letterbox_start:]), \
        "Expected bottom 32 rows to be black (letterbox band)"


def test_get_cached_preview_miss(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    result = pc.get_cached_preview("nonexistent_game")
    assert result is None


def test_get_cache_metadata(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    game_id = "test_game"
    pc.save_cached_preview(game_id, _make_png_bytes(), etag='"xyz"', last_modified="Mon, 01 Jan 2024 12:00:00 GMT")

    meta = pc.get_cache_metadata(game_id)
    assert meta is not None
    assert meta["etag"] == '"xyz"'
    assert meta["last_modified"] == "Mon, 01 Jan 2024 12:00:00 GMT"
    assert "fetch_time" in meta


def test_is_cache_expired_fresh(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    game_id = "test_game"
    pc.save_cached_preview(game_id, _make_png_bytes(), etag='"e"', last_modified="")
    assert pc.is_cache_expired(game_id, max_age_hours=24) is False


def test_is_cache_expired_old(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    game_id = "test_game"
    pc.save_cached_preview(game_id, _make_png_bytes(), etag='"e"', last_modified="")

    meta_path = os.path.join(cache_dir, f"{game_id}.meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    meta["fetch_time"] = time.time() - 25 * 3600  # 25 hours ago
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    assert pc.is_cache_expired(game_id, max_age_hours=24) is True


def test_is_cache_expired_missing(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    assert pc.is_cache_expired("no_such_game", max_age_hours=24) is True


def test_update_fetch_time(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    game_id = "test_game"
    pc.save_cached_preview(game_id, _make_png_bytes(), etag='"e"', last_modified="")

    meta_path = os.path.join(cache_dir, f"{game_id}.meta.json")
    with open(meta_path) as f:
        meta_before = json.load(f)

    time.sleep(0.01)
    pc.update_fetch_time(game_id)

    with open(meta_path) as f:
        meta_after = json.load(f)

    assert meta_after["fetch_time"] > meta_before["fetch_time"]


def test_get_cached_preview_corrupted_image(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    game_id = "corrupt_game"
    os.makedirs(cache_dir, exist_ok=True)
    img_path = os.path.join(cache_dir, f"{game_id}.png")
    with open(img_path, "wb") as f:
        f.write(b"this is not a valid png file garbage bytes")
    result = pc.get_cached_preview(game_id)
    assert result is None


def test_update_fetch_time_missing_game_id(cache_dir):
    from preview_cache import PreviewCache
    pc = PreviewCache(cache_dir)
    # Should return None silently without raising
    result = pc.update_fetch_time("nonexistent_game")
    assert result is None
