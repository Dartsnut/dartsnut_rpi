"""Integration tests for load_game_list() preview fallback."""
import io
import json
import os
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


def _make_png_bytes():
    img = Image.new("RGB", (128, 128), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_conf(tmp_path, name, preview=None, game_id=None, community_id=None):
    """Create an app directory with conf.json."""
    app_dir = tmp_path / "apps" / name
    app_dir.mkdir(parents=True)
    conf = {"type": "game", "name": name}
    if game_id is not None:
        conf["id"] = game_id
    if community_id is not None:
        conf["community_id"] = community_id
    if preview is not None:
        conf["preview"] = preview
    (app_dir / "conf.json").write_text(json.dumps(conf))
    return conf


def test_valid_conf_preview_uses_existing(tmp_path):
    """Game with valid base64 preview uses it directly (no fallback)."""
    import base64
    img = Image.new("RGB", (128, 128), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    _make_conf(tmp_path, "mygame", preview=[b64], game_id="mygame")

    mock_cache = MagicMock()
    mock_worker = MagicMock()

    with patch("game_lifecycle._preview_cache", mock_cache), \
         patch("game_lifecycle._validation_worker", mock_worker), \
         patch("os.getcwd", return_value=str(tmp_path)):
        import game_lifecycle
        games = game_lifecycle.load_game_list()

    assert len(games) == 1
    assert games[0]["preview"] is not None
    mock_cache.get_cached_preview.assert_not_called()
    mock_worker.submit.assert_not_called()


def test_missing_preview_cache_hit_fresh(tmp_path):
    """No preview in conf.json + fresh cache → use cached image, no worker submit."""
    _make_conf(tmp_path, "mygame", game_id="mygame")

    fake_cached = [bytearray(128 * 160 * 3)]
    mock_cache = MagicMock()
    mock_cache.get_cached_preview.return_value = fake_cached
    mock_cache.is_cache_expired.return_value = False
    mock_worker = MagicMock()

    with patch("game_lifecycle._preview_cache", mock_cache), \
         patch("game_lifecycle._validation_worker", mock_worker), \
         patch("os.getcwd", return_value=str(tmp_path)):
        import game_lifecycle
        games = game_lifecycle.load_game_list()

    assert games[0]["preview"] == fake_cached
    mock_worker.submit.assert_not_called()


def test_missing_preview_cache_hit_expired(tmp_path):
    """No preview + expired cache → use cached image + submit VALIDATE_EXPIRED."""
    _make_conf(tmp_path, "mygame", game_id="mygame")

    fake_cached = [bytearray(128 * 160 * 3)]
    mock_cache = MagicMock()
    mock_cache.get_cached_preview.return_value = fake_cached
    mock_cache.is_cache_expired.return_value = True
    mock_worker = MagicMock()

    with patch("game_lifecycle._preview_cache", mock_cache), \
         patch("game_lifecycle._validation_worker", mock_worker), \
         patch("os.getcwd", return_value=str(tmp_path)):
        import game_lifecycle
        from validation_worker import VALIDATE_EXPIRED
        games = game_lifecycle.load_game_list()

    assert games[0]["preview"] == fake_cached
    mock_worker.submit.assert_called_once_with(
        "mygame", priority=VALIDATE_EXPIRED, callback=game_lifecycle._on_preview_updated
    )


def test_missing_preview_cache_miss(tmp_path):
    """No preview + no cache → placeholder + submit FETCH_MISSING."""
    _make_conf(tmp_path, "mygame", game_id="mygame")

    mock_cache = MagicMock()
    mock_cache.get_cached_preview.return_value = None
    mock_worker = MagicMock()

    with patch("game_lifecycle._preview_cache", mock_cache), \
         patch("game_lifecycle._validation_worker", mock_worker), \
         patch("os.getcwd", return_value=str(tmp_path)):
        import game_lifecycle
        from validation_worker import FETCH_MISSING
        games = game_lifecycle.load_game_list()

    assert games[0]["preview"] is not None
    assert len(games[0]["preview"]) == 1
    mock_worker.submit.assert_called_once_with(
        "mygame", priority=FETCH_MISSING, callback=game_lifecycle._on_preview_updated
    )


def test_missing_preview_no_game_id(tmp_path):
    """No preview + no id → placeholder 'Preview unavailable', no worker submit."""
    app_dir = tmp_path / "apps" / "mygame"
    app_dir.mkdir(parents=True)
    conf = {"type": "game", "name": "My Game"}  # no id, no community_id
    (app_dir / "conf.json").write_text(json.dumps(conf))

    mock_cache = MagicMock()
    mock_worker = MagicMock()

    with patch("game_lifecycle._preview_cache", mock_cache), \
         patch("game_lifecycle._validation_worker", mock_worker), \
         patch("os.getcwd", return_value=str(tmp_path)):
        import game_lifecycle
        games = game_lifecycle.load_game_list()

    assert games[0]["preview"] is not None
    mock_worker.submit.assert_not_called()


def test_shutdown_preview_worker_calls_shutdown():
    """shutdown_preview_worker() shuts down and clears the global worker."""
    import game_lifecycle
    mock_worker = MagicMock()

    with patch.object(game_lifecycle, "_validation_worker", mock_worker):
        game_lifecycle.shutdown_preview_worker()
        assert game_lifecycle._validation_worker is None

    mock_worker.shutdown.assert_called_once_with(wait=True, timeout=5)


def test_on_preview_updated_updates_game_list_cache():
    """_on_preview_updated() patches the matching entry in _game_list_cache in place."""
    import game_lifecycle

    new_preview = [bytearray(128 * 160 * 3)]
    fake_cache = [{"id": "mygame", "preview": None}]

    with patch.object(game_lifecycle, "_game_list_cache", fake_cache):
        game_lifecycle._on_preview_updated("mygame", new_preview)

    assert fake_cache[0]["preview"] == new_preview
