"""Tests for community_api module."""
import configparser
import os
import pytest
from unittest.mock import patch, MagicMock
import requests

from community_api import CommunityApiConfig, CommunityApiClient, PreviewNotFound


# ---------------------------------------------------------------------------
# CommunityApiConfig tests
# ---------------------------------------------------------------------------

def test_load_config_missing_file_uses_defaults(tmp_path):
    cfg = CommunityApiConfig.load(path=str(tmp_path / "nonexistent.conf"))
    assert cfg.base_url == "https://api.dartsnut.com"
    assert cfg.timeout == 10


def test_load_config_does_not_create_file_when_missing(tmp_path):
    conf_path = tmp_path / "new_subdir" / "community_api.conf"
    cfg = CommunityApiConfig.load(path=str(conf_path))
    assert not conf_path.exists()
    assert cfg.base_url == "https://api.dartsnut.com"
    assert cfg.image_base_url == ""


def test_load_config_reads_configured_hosts_verbatim(tmp_path):
    conf_path = tmp_path / "community_api.conf"
    conf_path.write_text(
        "[community_api]\n"
        "base_url = https://api.example.test\n"
        "image_base_url = https://images.example.test\n"
        "timeout = 5\n"
    )
    cfg = CommunityApiConfig.load(path=str(conf_path))
    assert cfg.base_url == "https://api.example.test"
    assert cfg.image_base_url == "https://images.example.test"


def test_load_config_reads_image_base_url(tmp_path):
    conf_path = tmp_path / "community_api.conf"
    conf_path.write_text(
        "[community_api]\n"
        "base_url = https://test.example.com\n"
        "image_base_url = https://images.test.example.com\n"
        "timeout = 5\n"
    )
    cfg = CommunityApiConfig.load(path=str(conf_path))
    assert cfg.image_base_url == "https://images.test.example.com"


def test_load_config_from_ini_file(tmp_path):
    conf_path = tmp_path / "community_api.conf"
    conf_path.write_text(
        "[community_api]\n"
        "base_url = https://test.example.com\n"
        "timeout = 5\n"
    )
    cfg = CommunityApiConfig.load(path=str(conf_path))
    assert cfg.base_url == "https://test.example.com"
    assert cfg.timeout == 5


# ---------------------------------------------------------------------------
# CommunityApiClient tests
# ---------------------------------------------------------------------------

def _make_client(base_url="https://test.example.com"):
    cfg = CommunityApiConfig(base_url=base_url, timeout=5)
    return CommunityApiClient(config=cfg)


def test_fetch_preview_returns_bytes_on_200():
    client = _make_client()

    metadata_response = MagicMock()
    metadata_response.status_code = 200
    metadata_response.json.return_value = {
        "data": {
            "game_id": "game123",
            "game_name": "Game 123",
            "main_cover": "https://cdn.example.com/cover.png",
            "preview": ["https://cdn.example.com/game123.png"],
        }
    }
    metadata_response.raise_for_status = MagicMock()

    image_response = MagicMock()
    image_response.status_code = 200
    image_response.content = b"PNG_BYTES"
    image_response.headers = {"ETag": '"v1"'}
    image_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", side_effect=[metadata_response, image_response]) as mock_get:
        result = client.fetch_preview("game123")

    assert result == b"PNG_BYTES"
    assert mock_get.call_args_list[0][0][0] == "https://test.example.com/mobile/game/get-detail"
    assert mock_get.call_args_list[0][1]["params"]["game_id"] == "game123"
    assert mock_get.call_args_list[1][0][0] == "https://cdn.example.com/cover.png"


def test_fetch_preview_raises_preview_not_found_on_404():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response):
        with pytest.raises(PreviewNotFound):
            client.fetch_preview("no_such_game")


def test_fetch_preview_raises_http_error_on_500():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")

    with patch.object(client._session, "get", return_value=mock_response):
        with pytest.raises(requests.exceptions.HTTPError):
            client.fetch_preview("game123")


def test_fetch_preview_sends_conditional_headers():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"bytes"
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response) as mock_get:
        client.fetch_preview_image(
            "https://cdn.example.com/game123.png",
            etag='"etag_val"',
            last_modified="some_date",
        )

    call_kwargs = mock_get.call_args[1]
    headers = call_kwargs.get("headers", {})
    assert headers.get("If-None-Match") == '"etag_val"'
    assert headers.get("If-Modified-Since") == "some_date"


def test_fetch_game_metadata_sends_token_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _make_client()

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "game_id": "01dartgame",
            "game_name": "01 Darts Game",
            "main_cover": "cover.png",
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response) as mock_get:
        client.fetch_game_metadata("01dartgame")

    assert mock_get.call_args[1]["headers"] == {"Token": "abc"}


def test_fetch_preview_image_does_not_send_token_to_cdn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _make_client()

    from runtime.api_token_store import preserve_remote_user_token

    preserve_remote_user_token({"token": "abc"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"bytes"
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response) as mock_get:
        client.fetch_preview_image("https://cdn.example.com/game123.png")

    assert mock_get.call_args[1]["headers"] == {}


def test_fetch_game_metadata_uses_pic_128_url_for_preview_url():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "game_id": "01dartgame",
            "game_name": "01 Darts Game",
            "main_cover": "cover.png",
            "pic_128_url": "pic-128.png",
            "preview": ["preview-1.png", "preview-2.png"],
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response):
        meta = client.fetch_game_metadata("01dartgame")

    assert meta["id"] == "01dartgame"
    assert meta["name"] == "01 Darts Game"
    assert meta["main_cover"] == "pic-128.png"
    assert meta["preview_urls"] == ["pic-128.png"]


def test_fetch_game_metadata_falls_back_to_main_cover_when_pic_128_url_empty():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "game_id": "01dartgame",
            "game_name": "01 Darts Game",
            "main_cover": "cover.png",
            "pic_128_url": "",
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response):
        meta = client.fetch_game_metadata("01dartgame")

    assert meta["main_cover"] == "cover.png"
    assert meta["preview_urls"] == ["cover.png"]


def test_fetch_game_metadata_handles_missing_main_cover():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "game_id": "01dartgame",
            "game_name": "01 Darts Game",
            "preview": ["preview-1.png"],
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_response):
        meta = client.fetch_game_metadata("01dartgame")

    assert meta["main_cover"] == ""
    assert meta["preview_urls"] == []


def test_build_preview_url_uses_image_base_for_relative_paths():
    cfg = CommunityApiConfig(base_url="https://api.example.com", image_base_url="https://img.example.com/assets")
    client = CommunityApiClient(config=cfg)
    assert client.build_preview_url("/cover.png") == "https://img.example.com/assets/cover.png"


def test_get_etag_returns_header_value():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.headers = {"ETag": '"abc123"'}
    assert client.get_etag(mock_response) == '"abc123"'


def test_get_etag_returns_none_when_missing():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.headers = {}
    assert client.get_etag(mock_response) is None


def test_get_last_modified_returns_header_value():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.headers = {"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    assert client.get_last_modified(mock_response) == "Wed, 01 Jan 2025 00:00:00 GMT"


def test_get_last_modified_returns_none_when_missing():
    client = _make_client()
    mock_response = MagicMock()
    mock_response.headers = {}
    assert client.get_last_modified(mock_response) is None


# ---------------------------------------------------------------------------
# New tests
# ---------------------------------------------------------------------------

def test_fetch_preview_returns_none_on_304():
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 304
    mock_response.headers = {"ETag": '"abc"'}

    with patch.object(client._session, "get", return_value=mock_response):
        status, data, headers = client.fetch_preview_image("https://cdn.example.com/game123.png", etag='"abc"')

    assert status == 304
    assert data is None
    assert headers["ETag"] == '"abc"'


def test_fetch_preview_propagates_connection_error():
    client = _make_client()

    with patch.object(client._session, "get", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(requests.exceptions.ConnectionError):
            client.fetch_preview("game123")


def test_fetch_preview_raises_value_error_for_game_id_with_slash():
    client = _make_client()
    with pytest.raises(ValueError):
        client.fetch_preview("game/123")


def test_fetch_preview_raises_value_error_for_empty_game_id():
    client = _make_client()
    with pytest.raises(ValueError):
        client.fetch_preview("")
