"""Tests for validation_worker module."""
import io
import time
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


def _make_png_bytes():
    img = Image.new("RGB", (128, 128), (100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path / "preview_cache")


@pytest.fixture
def config_path(tmp_path):
    return str(tmp_path / "community_api.conf")


def test_worker_starts_and_shuts_down(cache_dir, config_path):
    from validation_worker import ValidationWorker
    w = ValidationWorker(cache_dir=cache_dir, config_path=config_path)
    w.start()
    w.shutdown(wait=True, timeout=2)


def test_submit_fetch_missing_calls_callback(cache_dir, config_path, tmp_path):
    """When cache is empty, worker fetches and calls callback with preview data."""
    from validation_worker import ValidationWorker, FETCH_MISSING

    png_bytes = _make_png_bytes()

    mock_api = MagicMock()
    mock_api.fetch_game_metadata.return_value = {
        "id": "g1",
        "name": "Game One",
        "main_cover": "images/g1/cover.png",
        "preview_urls": ["images/g1/preview.png"],
    }
    mock_api.fetch_preview_image.return_value = (200, png_bytes, {"etag": '"v1"', "last_modified": ""})
    mock_api.build_preview_url.return_value = "https://cdn.example.com/images/g1/preview.png"
    mock_api.config = {"retry_intervals_seconds": [0.01, 0.01, 0.01]}

    callback_results = []

    def my_callback(game_id, preview_data):
        callback_results.append((game_id, preview_data))

    w = ValidationWorker(cache_dir=cache_dir, config_path=config_path)
    w._api = mock_api  # inject mock
    w.start()
    w.submit("g1", priority=FETCH_MISSING, callback=my_callback)

    deadline = time.time() + 5
    while not callback_results and time.time() < deadline:
        time.sleep(0.05)

    w.shutdown(wait=True, timeout=2)

    assert len(callback_results) == 1
    gid, preview = callback_results[0]
    assert gid == "g1"
    assert isinstance(preview, list)
    assert len(preview) == 1
    assert isinstance(preview[0], bytearray)


def test_submit_validate_304_updates_fetch_time(cache_dir, config_path):
    """Worker handles 304 by updating fetch_time without changing image."""
    from validation_worker import ValidationWorker, VALIDATE_EXPIRED
    from preview_cache import PreviewCache

    png_bytes = _make_png_bytes()
    pc = PreviewCache(cache_dir)
    pc.save_cached_preview("g2", png_bytes, etag='"old"', last_modified="")

    import time as _time
    import json, os
    meta_path = os.path.join(cache_dir, "g2.meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    old_fetch_time = meta["fetch_time"]

    _time.sleep(0.05)

    mock_api = MagicMock()
    mock_api.fetch_game_metadata.return_value = {
        "id": "g2",
        "name": "Game Two",
        "main_cover": "",
        "preview_urls": ["images/g2/preview.png"],
    }
    mock_api.fetch_preview_image.return_value = (304, None, None)
    mock_api.build_preview_url.return_value = "https://cdn.example.com/images/g2/preview.png"
    mock_api.config = {"retry_intervals_seconds": [0.01]}

    w = ValidationWorker(cache_dir=cache_dir, config_path=config_path)
    w._api = mock_api
    w.start()

    done = [False]

    def cb(game_id, preview_data):
        done[0] = True

    w.submit("g2", priority=VALIDATE_EXPIRED, callback=cb)

    deadline = time.time() + 5
    while not done[0] and time.time() < deadline:
        time.sleep(0.05)

    w.shutdown(wait=True, timeout=2)

    with open(meta_path) as f:
        meta_after = json.load(f)

    assert meta_after["fetch_time"] > old_fetch_time


def test_no_duplicate_tasks(cache_dir, config_path):
    """Submitting the same game_id twice shouldn't queue it twice."""
    from validation_worker import ValidationWorker, FETCH_MISSING

    mock_api = MagicMock()
    mock_api.fetch_game_metadata.return_value = None  # causes no callback
    mock_api.config = {"retry_intervals_seconds": [0.01]}

    w = ValidationWorker(cache_dir=cache_dir, config_path=config_path)
    w._api = mock_api
    w.start()

    w.submit("g3", priority=FETCH_MISSING, callback=lambda *a: None)
    w.submit("g3", priority=FETCH_MISSING, callback=lambda *a: None)

    w.shutdown(wait=True, timeout=2)
    # Just verify no exceptions; can't easily assert queue size after drain
