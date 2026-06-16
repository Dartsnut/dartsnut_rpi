"""Tests for community_api module."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def config_file(tmp_path):
    conf_path = tmp_path / "community_api.conf"
    conf_path.write_text(json.dumps({
        "api_base_url": "https://test.example.com",
        "timeout_seconds": 5,
        "cache_max_age_hours": 24,
    }))
    return str(conf_path)


def test_load_config_defaults():
    from community_api import CommunityApiConfig
    cfg = CommunityApiConfig()
    assert cfg.api_base_url == "https://api.dartsnut.com"
    assert cfg.timeout_seconds == 10
    assert cfg.cache_max_age_hours == 24


def test_load_config_from_file(config_file):
    from community_api import CommunityApiConfig
    cfg = CommunityApiConfig(config_file)
    assert cfg.api_base_url == "https://test.example.com"
    assert cfg.timeout_seconds == 5
    assert cfg.cache_max_age_hours == 24


def test_fetch_preview_success():
    from community_api import CommunityApiClient
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"PNG_BYTES_HERE"
    mock_response.headers = {"ETag": '"abc123"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}

    with patch("community_api.requests.get", return_value=mock_response) as mock_get:
        client = CommunityApiClient(api_base_url="https://test.example.com", timeout_seconds=5)
        result = client.fetch_preview("game123")

    assert result is not None
    assert result["image_data"] == b"PNG_BYTES_HERE"
    assert result["etag"] == '"abc123"'
    assert result["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
    mock_get.assert_called_once()
    call_url = mock_get.call_args[0][0]
    assert "game123" in call_url


def test_fetch_preview_not_found():
    from community_api import CommunityApiClient, PreviewNotFound
    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("community_api.requests.get", return_value=mock_response):
        client = CommunityApiClient(api_base_url="https://test.example.com", timeout_seconds=5)
        with pytest.raises(PreviewNotFound):
            client.fetch_preview("no_such_game")


def test_fetch_preview_not_modified():
    from community_api import CommunityApiClient
    mock_response = MagicMock()
    mock_response.status_code = 304
    mock_response.content = b""
    mock_response.headers = {}

    with patch("community_api.requests.get", return_value=mock_response):
        client = CommunityApiClient(api_base_url="https://test.example.com", timeout_seconds=5)
        result = client.fetch_preview("game123", etag='"old"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT")

    assert result is None  # None = not modified, use cached


def test_fetch_preview_passes_conditional_headers():
    from community_api import CommunityApiClient
    mock_response = MagicMock()
    mock_response.status_code = 304
    mock_response.content = b""
    mock_response.headers = {}

    with patch("community_api.requests.get", return_value=mock_response) as mock_get:
        client = CommunityApiClient(api_base_url="https://test.example.com", timeout_seconds=5)
        client.fetch_preview("game123", etag='"etag_val"', last_modified="some_date")

    call_kwargs = mock_get.call_args[1]
    headers = call_kwargs.get("headers", {})
    assert headers.get("If-None-Match") == '"etag_val"'
    assert headers.get("If-Modified-Since") == "some_date"


def test_fetch_preview_server_error_raises():
    from community_api import CommunityApiClient
    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch("community_api.requests.get", return_value=mock_response):
        client = CommunityApiClient(api_base_url="https://test.example.com", timeout_seconds=5)
        with pytest.raises(Exception):
            client.fetch_preview("game123")


def test_fetch_preview_network_error_raises():
    from community_api import CommunityApiClient
    import requests as req_lib
    with patch("community_api.requests.get", side_effect=req_lib.RequestException("timeout")):
        client = CommunityApiClient(api_base_url="https://test.example.com", timeout_seconds=5)
        with pytest.raises(Exception):
            client.fetch_preview("game123")
