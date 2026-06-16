"""Background thread worker that fetches and validates cached game preview images."""
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from community_api import CommunityApiClient, CommunityApiConfig
from preview_cache import PreviewCache

_log = logging.getLogger(__name__)

FETCH_MISSING = 1    # No cache, user waiting
VALIDATE_EXPIRED = 2  # Cache expired, needs revalidation
VALIDATE_FRESH = 3   # Proactive startup validation

_RETRY_INTERVALS = [30, 120, 600]


class _RealApiAdapter:
    """Wraps CommunityApiClient to expose the interface expected by ValidationWorker._process_game."""

    def __init__(self, client: CommunityApiClient):
        self._client = client
        self.config = {"retry_intervals_seconds": _RETRY_INTERVALS}

    def fetch_game_metadata(self, game_id: str) -> Optional[dict]:
        # Real API doesn't have metadata endpoint yet; return minimal stub so preview fetch proceeds.
        return {"id": game_id, "name": game_id, "main_cover": "", "preview_urls": [game_id]}

    def build_preview_url(self, path: str) -> str:
        # path is used as game_id for fetch_preview
        return path

    def fetch_preview_image(self, game_id: str, etag: Optional[str] = None, last_modified: Optional[str] = None):
        """Returns (status, bytes_or_none, headers_or_none)."""
        from community_api import PreviewNotFound
        import requests
        try:
            data = self._client.fetch_preview(game_id, etag=etag, last_modified=last_modified)
            if data is None:
                return (304, None, None)
            return (200, data, {})
        except PreviewNotFound:
            return (404, None, None)
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            return (code, None, None)
        except Exception as e:
            _log.error("[Preview] Unexpected error fetching %s: %s", game_id, e)
            return (0, None, None)


class _Task:
    def __init__(self, game_id: str, priority: int, callback: Optional[Callable]):
        self.game_id = game_id
        self.priority = priority
        self.callback = callback

    def __lt__(self, other):
        return self.priority < other.priority


class ValidationWorker:
    def __init__(self, max_workers: int = 2, cache_dir: Optional[str] = None, config_path: Optional[str] = None):
        cache_kwargs = {}
        if cache_dir:
            cache_kwargs["cache_dir"] = cache_dir
        self._cache = PreviewCache(**cache_kwargs)

        config = CommunityApiConfig.load(config_path) if config_path else CommunityApiConfig.load()
        real_client = CommunityApiClient(config=config)
        self._api = _RealApiAdapter(real_client)

        self._max_workers = max_workers
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._inflight: set = set()
        self._inflight_lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._shutdown_event = threading.Event()
        self._dispatcher: Optional[threading.Thread] = None

    def start(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True, name="preview-dispatcher")
        self._dispatcher.start()

    def submit(self, game_id: str, priority: int, callback: Optional[Callable] = None) -> None:
        with self._inflight_lock:
            if game_id in self._inflight:
                _log.debug("[Preview] Task for %s already in-flight, skipping", game_id)
                return
            self._inflight.add(game_id)
        self._queue.put((priority, _Task(game_id, priority, callback)))

    def shutdown(self, wait: bool = True, timeout: float = 5) -> None:
        self._shutdown_event.set()
        self._queue.put((0, None))
        if self._dispatcher and wait:
            self._dispatcher.join(timeout=timeout)
        if self._executor:
            self._executor.shutdown(wait=wait, cancel_futures=True)

    def _dispatch_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                _, task = self._queue.get(timeout=1)
                if task is None:
                    break
                self._executor.submit(self._run_task, task)
            except queue.Empty:
                continue
            except Exception as e:
                _log.error("[Preview] Dispatcher error: %s", e)

    def _run_task(self, task: _Task) -> None:
        game_id = task.game_id
        try:
            self._process_game(game_id, task.callback)
        except Exception as e:
            _log.error("[Preview] Unhandled error processing %s: %s", game_id, e)
        finally:
            with self._inflight_lock:
                self._inflight.discard(game_id)

    def _process_game(self, game_id: str, callback: Optional[Callable]) -> None:
        meta = self._api.fetch_game_metadata(game_id)
        if not meta:
            _log.warning("[Preview] No metadata for game %s; aborting preview fetch", game_id)
            return

        preview_urls = meta.get("preview_urls") or []
        if not preview_urls:
            cover = meta.get("main_cover")
            if cover:
                preview_urls = [cover]

        if not preview_urls:
            _log.warning("[Preview] No preview URLs for game %s", game_id)
            return

        image_url = self._api.build_preview_url(preview_urls[0])

        cached_meta = self._cache.get_cache_metadata(game_id)
        etag = cached_meta.get("etag") if cached_meta else None
        last_modified = cached_meta.get("last_modified") if cached_meta else None

        # Get retry intervals from config (supports both dict mock and real config object)
        api_config = self._api.config
        try:
            retry_intervals = api_config.get("retry_intervals_seconds", _RETRY_INTERVALS)
        except AttributeError:
            retry_intervals = getattr(api_config, "retry_intervals_seconds", _RETRY_INTERVALS)

        status, image_data, response_headers = 0, None, None

        for attempt, delay in enumerate([0] + list(retry_intervals)):
            if attempt > 0:
                import time
                _log.warning("[Preview] Retrying fetch for game %s (attempt %d/%d)", game_id, attempt + 1, len(retry_intervals) + 1)
                time.sleep(delay)

            status, image_data, response_headers = self._api.fetch_preview_image(
                image_url, etag=etag, last_modified=last_modified
            )

            if status in (200, 304):
                break
            if status == 404:
                _log.warning("[Preview] Game %s has no preview (404)", game_id)
                return
            if status in (401, 403):
                _log.error("[Preview] Auth error fetching preview for %s (%d)", game_id, status)
                return

        preview_data = None

        if status == 304:
            _log.info("[Preview] Cache still fresh for game %s (304)", game_id)
            self._cache.update_fetch_time(game_id)
            preview_data = self._cache.get_cached_preview(game_id)

        elif status == 200 and image_data:
            try:
                self._cache.save_cached_preview(
                    game_id,
                    image_data,
                    etag=response_headers.get("etag", "") if response_headers else "",
                    last_modified=response_headers.get("last_modified", "") if response_headers else "",
                )
                preview_data = self._cache.get_cached_preview(game_id)
                _log.info("[Preview] Fetched and cached preview for game %s", game_id)
            except Exception as e:
                _log.error("[Preview] Failed to save cache for game %s: %s", game_id, e)
        else:
            _log.error("[Preview] Failed to fetch preview for game %s after retries (status=%d)", game_id, status)

        if preview_data and callback:
            try:
                callback(game_id, preview_data)
            except Exception as e:
                _log.error("[Preview] Callback error for game %s: %s", game_id, e)
