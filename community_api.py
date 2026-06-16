"""HTTP client for fetching game preview images from the community API."""
import json
import logging
import os
from typing import Dict, Optional

import requests

_log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.dartsnut/community_api.conf")
_DEFAULT_API_BASE_URL = "https://api.dartsnut.com"
_DEFAULT_TIMEOUT_SECONDS = 10
_DEFAULT_CACHE_MAX_AGE_HOURS = 24


class PreviewNotFound(Exception):
    """Raised when the community API returns 404 for a game preview."""


class CommunityApiConfig:
    """Loads configuration from ~/.dartsnut/community_api.conf with sensible defaults."""

    def __init__(self, config_path: str = _DEFAULT_CONFIG_PATH):
        self.api_base_url = _DEFAULT_API_BASE_URL
        self.timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        self.cache_max_age_hours = _DEFAULT_CACHE_MAX_AGE_HOURS
        self._load(config_path)

    def _load(self, config_path: str) -> None:
        if not os.path.isfile(config_path):
            return
        try:
            with open(config_path) as f:
                data = json.load(f)
            self.api_base_url = data.get("api_base_url", self.api_base_url)
            self.timeout_seconds = data.get("timeout_seconds", self.timeout_seconds)
            self.cache_max_age_hours = data.get("cache_max_age_hours", self.cache_max_age_hours)
        except Exception as e:
            _log.warning("[CommunityAPI] Failed to load config from %s: %s", config_path, e)


class CommunityApiClient:
    """Fetches preview images from the dartsnut community API."""

    def __init__(
        self,
        api_base_url: str = _DEFAULT_API_BASE_URL,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ):
        self._base_url = api_base_url.rstrip("/")
        self._timeout = timeout_seconds

    def fetch_preview(
        self,
        game_id: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Fetch preview image for game_id from community API.

        Returns:
            dict with keys: image_data (bytes), etag (str), last_modified (str)
            None if server returns 304 Not Modified (use cached version)

        Raises:
            PreviewNotFound: if server returns 404
            requests.RequestException: on network errors
            Exception: on other HTTP errors (5xx, etc.)
        """
        url = f"{self._base_url}/games/{game_id}/preview"
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        _log.debug("[CommunityAPI] Fetching preview for game %s from %s", game_id, url)
        response = requests.get(url, headers=headers, timeout=self._timeout)

        if response.status_code == 304:
            _log.debug("[CommunityAPI] Cache still valid for game %s (304)", game_id)
            return None

        if response.status_code == 404:
            raise PreviewNotFound(f"No preview found for game {game_id!r}")

        if response.status_code != 200:
            raise Exception(
                f"Community API returned {response.status_code} for game {game_id!r}"
            )

        return {
            "image_data": response.content,
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
        }
