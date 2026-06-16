"""HTTP client for fetching game preview images from the community API."""
import configparser
import logging
import os
from typing import Optional

import requests

_log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.dartsnut/community_api.conf")


class PreviewNotFound(Exception):
    """Raised when the community API returns 404 for a game preview."""


class CommunityApiConfig:
    """Loads configuration from ~/.dartsnut/community_api.conf (INI format) with sensible defaults."""

    def __init__(self, base_url: str = "https://api.dartsnut.community", timeout: int = 10, max_retries: int = 3):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

    @classmethod
    def load(cls, path: str = None) -> "CommunityApiConfig":
        """Load config from INI file. Uses defaults if file is missing."""
        config_path = path or _DEFAULT_CONFIG_PATH
        instance = cls()
        if not os.path.isfile(config_path):
            return instance
        try:
            parser = configparser.ConfigParser()
            parser.read(config_path)
            section = "community_api"
            if parser.has_section(section):
                if parser.has_option(section, "base_url"):
                    instance.base_url = parser.get(section, "base_url")
                if parser.has_option(section, "timeout"):
                    instance.timeout = parser.getint(section, "timeout")
                if parser.has_option(section, "max_retries"):
                    instance.max_retries = parser.getint(section, "max_retries")
        except Exception as e:
            _log.warning("[CommunityAPI] Failed to load config from %s: %s", config_path, e)
        return instance


class CommunityApiClient:
    """Fetches preview images from the dartsnut community API."""

    def __init__(self, config: CommunityApiConfig = None):
        if config is None:
            config = CommunityApiConfig.load()
        self._config = config

    def fetch_preview(
        self,
        game_id: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> bytes:
        """
        Fetch preview image for game_id from community API.

        Returns:
            Raw image bytes on 200.

        Raises:
            PreviewNotFound: if server returns 404
            requests.exceptions.HTTPError: on 4xx/5xx errors
        """
        url = f"{self._config.base_url.rstrip('/')}/games/{game_id}/preview"
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        _log.debug("[CommunityAPI] Fetching preview for game %s from %s", game_id, url)
        response = requests.get(url, headers=headers, timeout=self._config.timeout)

        if response.status_code == 404:
            raise PreviewNotFound(f"No preview found for game {game_id!r}")

        response.raise_for_status()

        return response.content

    def get_etag(self, response) -> Optional[str]:
        """Return the ETag header value from a response, or None."""
        return response.headers.get("ETag")

    def get_last_modified(self, response) -> Optional[str]:
        """Return the Last-Modified header value from a response, or None."""
        return response.headers.get("Last-Modified")
