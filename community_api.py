"""HTTP client for fetching game preview images from the community API."""
import configparser
import logging
import os
from urllib.parse import urljoin
from typing import Optional

import requests

_log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.expanduser("~/.dartsnut/community_api.conf")
_LEGACY_BASE_URL = "https://api.dartsnut.community"
_LEGACY_IMAGE_BASE_URL = "https://images.dartsnut.community"


class PreviewNotFound(Exception):
    """Raised when the community API returns 404 for a game preview."""


class CommunityApiConfig:
    """Loads configuration from ~/.dartsnut/community_api.conf (INI format) with sensible defaults."""

    def __init__(
        self,
        base_url: str = "https://api.dartsnut.com",
        image_base_url: str = "",
        timeout: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
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
                    instance.base_url = parser.get(section, "base_url").rstrip("/")
                if parser.has_option(section, "image_base_url"):
                    instance.image_base_url = parser.get(section, "image_base_url").rstrip("/")
                if parser.has_option(section, "timeout"):
                    instance.timeout = parser.getint(section, "timeout")
                if instance.base_url == _LEGACY_BASE_URL:
                    instance.base_url = cls().base_url
                if instance.image_base_url == _LEGACY_IMAGE_BASE_URL:
                    instance.image_base_url = cls().image_base_url
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
        meta = self.fetch_game_metadata(game_id)
        preview_urls = meta.get("preview_urls") or []
        image_url = preview_urls[0] if preview_urls else meta.get("main_cover")
        if not image_url:
            raise PreviewNotFound(f"No preview found for game {game_id!r}")
        status, data, _headers = self.fetch_preview_image(
            image_url,
            etag=etag,
            last_modified=last_modified,
        )
        if status == 304:
            return None
        if status == 404:
            raise PreviewNotFound(f"No preview found for game {game_id!r}")
        return data

    def fetch_game_metadata(self, game_id: str) -> dict:
        """Return public community metadata for a game id using the community list API."""
        if not game_id or "/" in game_id:
            raise ValueError(f"Invalid game_id: {game_id!r}")

        url = f"{self._config.base_url}/community/game/list"
        response = self._session.get(
            url,
            params={"page": 1, "size": 20, "game_name": game_id},
            timeout=self._config.timeout,
        )
        if response.status_code == 404:
            raise PreviewNotFound(f"No game found for {game_id!r}")
        response.raise_for_status()
        payload = response.json()
        rows = ((payload.get("data") or {}).get("list") or []) if isinstance(payload, dict) else []
        normalized = str(game_id).strip().lower()
        match = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_game_id = str(row.get("game_id") or "").strip().lower()
            if row_game_id == normalized:
                match = row
                break
        if match is None and len(rows) == 1 and isinstance(rows[0], dict):
            match = rows[0]
        if match is None:
            raise PreviewNotFound(f"No game found for {game_id!r}")

        preview_urls = []
        cover = str(match.get("main_cover") or "").strip()
        if cover:
            preview_urls.append(cover)
        return {
            "id": str(match.get("game_id") or game_id),
            "name": match.get("game_name") or game_id,
            "main_cover": cover,
            "preview_urls": preview_urls,
        }

    def build_preview_url(self, path: str) -> str:
        """Build an absolute preview image URL from API image fields."""
        raw = str(path or "").strip()
        if not raw:
            return ""
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        base = self._config.image_base_url or self._config.base_url
        return urljoin(f"{base.rstrip('/')}/", raw.lstrip("/"))

    def fetch_preview_image(
        self,
        image_url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ):
        """Fetch preview image URL and return (status, bytes_or_none, headers_or_none)."""
        url = self.build_preview_url(image_url)
        if not url:
            raise PreviewNotFound("Preview image URL is empty")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        _log.debug("[CommunityAPI] Fetching preview image from %s", url)
        response = self._session.get(url, headers=headers, timeout=self._config.timeout)

        if response.status_code == 304:
            _log.debug("[CommunityAPI] 304 Not Modified for preview image %s", url)
            return (304, None, dict(response.headers))

        if response.status_code == 404:
            return (404, None, dict(response.headers))

        response.raise_for_status()

        _log.debug("[CommunityAPI] Successfully fetched preview image (%d bytes)", len(response.content))
        return (response.status_code, response.content, dict(response.headers))

    @staticmethod
    def get_etag(response) -> Optional[str]:
        """Return the ETag header value from a response, or None."""
        return response.headers.get("ETag")

    @staticmethod
    def get_last_modified(response) -> Optional[str]:
        """Return the Last-Modified header value from a response, or None."""
        return response.headers.get("Last-Modified")
