"""Tests for community_api module."""
import configparser
import os
import pytest
from unittest.mock import patch, MagicMock
import requests


# ---------------------------------------------------------------------------
# CommunityApiConfig tests
# ---------------------------------------------------------------------------

def test_load_config_missing_file_uses_defaults(tmp_path):
    from community_api import CommunityApiConfig
    cfg = CommunityApiConfig.load(path=str(tmp_path / "nonexistent.conf"))
    assert cfg.base_url == "https://api.dartsnut.community"
    assert cfg.timeout == 10
    assert cfg.max_retries == 3


def test_load_config_from_ini_file(tmp_path):
    from community_api import CommunityApiConfig
    conf_path = tmp_path / "community_api.conf"
    conf_path.write_text(
        "[community_api]\n"
        "base_url = https://test.example.com\n"
        "timeout = 5\n"
        "max_retries = 1\n"
    )
    cfg = CommunityApiConfig.load(path=str(conf_path))
    assert cfg.base_url == "https://test.example.com"
    assert cfg.timeout == 5
    assert cfg.max_retries == 1


# ---------------------------------------------------------------------------
# CommunityApiClient tests
# ---------------------------------------------------------------------------

def _make_client(base_url="https://test.example.com"):
    from community_api import CommunityApiConfig, CommunityApiClient
    cfg = CommunityApiConfig(base_url=base_url, timeout=5, max_retries=1)
    return CommunityApiClient(config=cfg)


def test_fetch_preview_returns_bytes_on_200():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"PNG_BYTES"
    mock_response.raise_for_status = MagicMock()

    with patch("community_api.requests.get", return_value=mock_response) as mock_get:
        result = client.fetch_preview("game123")

    assert result == b"PNG_BYTES"
    mock_get.assert_called_once()
    call_url = mock_get.call_args[0][0]
    assert "game123" in call_url


def test_fetch_preview_raises_preview_not_found_on_404():
    from community_api import CommunityApiClient, PreviewNotFound
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock()

    with patch("community_api.requests.get", return_value=mock_response):
        with pytest.raises(PreviewNotFound):
            client.fetch_preview("no_such_game")


def test_fetch_preview_raises_http_error_on_500():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")

    with patch("community_api.requests.get", return_value=mock_response):
        with pytest.raises(requests.exceptions.HTTPError):
            client.fetch_preview("game123")


def test_fetch_preview_sends_conditional_headers():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"bytes"
    mock_response.raise_for_status = MagicMock()

    with patch("community_api.requests.get", return_value=mock_response) as mock_get:
        client.fetch_preview("game123", etag='"etag_val"', last_modified="some_date")

    call_kwargs = mock_get.call_args[1]
    headers = call_kwargs.get("headers", {})
    assert headers.get("If-None-Match") == '"etag_val"'
    assert headers.get("If-Modified-Since") == "some_date"


def test_get_etag_returns_header_value():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.headers = {"ETag": '"abc123"'}
    assert client.get_etag(mock_response) == '"abc123"'


def test_get_etag_returns_none_when_missing():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.headers = {}
    assert client.get_etag(mock_response) is None


def test_get_last_modified_returns_header_value():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.headers = {"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    assert client.get_last_modified(mock_response) == "Wed, 01 Jan 2025 00:00:00 GMT"


def test_get_last_modified_returns_none_when_missing():
    from community_api import CommunityApiClient
    client = _make_client()

    mock_response = MagicMock()
    mock_response.headers = {}
    assert client.get_last_modified(mock_response) is None
