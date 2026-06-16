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

    def __init__(
        self,
        base_url: str = "https://api.dartsnut.community",
        image_base_url: str = "https://images.dartsnut.community",
        timeout: int = 10,
    ):
        self.base_url = base_url
        self.image_base_url = image_base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def load(cls, path: str = None) -> "CommunityApiConfig":
        """Load config from INI file. Creates file with defaults if missing."""
        config_path = path or _DEFAULT_CONFIG_PATH
        instance = cls()
        if not os.path.isfile(config_path):
            # Create the file with defaults
            try:
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                parser = configparser.ConfigParser()
                section = "community_api"
                parser[section] = {
                    "base_url": instance.base_url,
                    "image_base_url": instance.image_base_url,
                    "timeout": str(instance.timeout),
                }
                with open(config_path, "w") as f:
                    parser.write(f)
            except (configparser.Error, ValueError, OSError) as e:
                _log.warning("[CommunityAPI] Failed to create config at %s: %s", config_path, e)
            return instance
        try:
            parser = configparser.ConfigParser()
            parser.read(config_path)
            section = "community_api"
            if parser.has_section(section):
                if parser.has_option(section, "base_url"):
                    instance.base_url = parser.get(section, "base_url")
                if parser.has_option(section, "image_base_url"):
                    instance.image_base_url = parser.get(section, "image_base_url").rstrip("/")
                if parser.has_option(section, "timeout"):
                    instance.timeout = parser.getint(section, "timeout")
        except (configparser.Error, ValueError, OSError) as e:
            _log.warning("[CommunityAPI] Failed to load config from %s: %s", config_path, e)
        return instance


class CommunityApiClient:
    """Fetches preview images from the dartsnut community API."""

    def __init__(self, config: CommunityApiConfig = None):
        if config is None:
            config = CommunityApiConfig.load()
        self._config = config
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "dartsnut-rpi/1.0"

    def fetch_preview(
        self,
        game_id: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Fetch preview image for game_id from community API.

        Returns:
            Raw image bytes on 200.
            None on 304 (not modified).

        Raises:
            ValueError: if game_id is empty or contains '/'
            PreviewNotFound: if server returns 404
            requests.exceptions.HTTPError: on 4xx/5xx errors
        """
        if not game_id or "/" in game_id:
            raise ValueError(f"Invalid game_id: {game_id!r}")

        url = f"{self._config.base_url.rstrip('/')}/games/{game_id}/preview"
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        _log.debug("[CommunityAPI] Fetching preview for game %s from %s", game_id, url)
        response = self._session.get(url, headers=headers, timeout=self._config.timeout)

        if response.status_code == 304:
            _log.debug("[CommunityAPI] 304 Not Modified for game %s (cache hit)", game_id)
            return None

        if response.status_code == 404:
            raise PreviewNotFound(f"No preview found for game {game_id!r}")

        response.raise_for_status()

        _log.debug("[CommunityAPI] Successfully fetched preview for game %s (%d bytes)", game_id, len(response.content))
        return response.content

    @staticmethod
    def get_etag(response) -> Optional[str]:
        """Return the ETag header value from a response, or None."""
        return response.headers.get("ETag")

    @staticmethod
    def get_last_modified(response) -> Optional[str]:
        """Return the Last-Modified header value from a response, or None."""
        return response.headers.get("Last-Modified")
