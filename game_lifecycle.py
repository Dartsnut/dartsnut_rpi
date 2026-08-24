"""Game lifecycle: start/term game process, load game list."""
import atexit
import base64
import io
import json
import logging
import os
import shutil
import threading
from multiprocessing import shared_memory
import subprocess
import requests

from core.helpers import (
    get_user_data_store_path,
    app_dir,
    app_python_command,
    subprocess_launch_kwargs,
    terminate_process_group,
)
from core.app_env import ensure_app_venv, install_app_tarball
from core.app_metadata import read_app_metadata, write_app_metadata
from core.retry import retry_with_backoff
from runtime.api_token_store import build_api_headers
from runtime.game_secret_store import get_pico8_key
from pico8_sync import sync_pico8_favourites
from python_websocket.user_data_operations import (
    start_game_tracking,
    stop_game_tracking,
    _load_user_data,
)
import assets
from PIL import Image

_log = logging.getLogger(__name__)

from preview_cache import PreviewCache
from validation_worker import ValidationWorker, FETCH_MISSING, VALIDATE_EXPIRED

_preview_cache: "PreviewCache | None" = None
_validation_worker: "ValidationWorker | None" = None
_worker_init_lock = threading.Lock()


def _ensure_worker_started() -> None:
    global _preview_cache, _validation_worker
    with _worker_init_lock:
        if _preview_cache is None:
            _preview_cache = PreviewCache()
        if _validation_worker is None:
            _validation_worker = ValidationWorker(max_workers=2)
            _validation_worker.start()


def shutdown_preview_worker() -> None:
    global _validation_worker
    if _validation_worker is not None:
        _validation_worker.shutdown(wait=True, timeout=5)
        _validation_worker = None


atexit.register(shutdown_preview_worker)


_game_list_cache: list = []
_game_list_cache_lock = threading.Lock()
_bad_preview_warning_keys: set[tuple[str, str]] = set()
_bad_preview_warning_lock = threading.Lock()


def _on_preview_updated(game_id: str, preview_data: list) -> None:
    with _game_list_cache_lock:
        for game in _game_list_cache:
            if game.get("id") == game_id or game.get("community_id") == game_id:
                game["preview"] = preview_data
                _log.info("[Preview] Updated in-memory preview for game %s", game_id)
                return
    _log.debug("[Preview] Received preview update for %s but game not in cache", game_id)


# Track in-flight game downloads (by game_id) to prevent duplicate concurrent downloads
_game_background_download_inflight = set()
_game_download_lock = threading.Lock()


def _sync_pico8_favourites_if_configured() -> None:
    key = get_pico8_key()
    if not key:
        return
    try:
        result = sync_pico8_favourites(key)
    except Exception as e:
        _log.warning("game: pico8 favourites sync failed: %s", e)
        return
    if result is not None and not result.ok:
        _log.warning("game: pico8 favourites sync failed: %s", result.message or "unknown error")


def _decode_game_preview_frames(preview_raw, game_label: str) -> list:
    """
    Decode base64-encoded preview images into RGB buffers for the game picker.

    Malformed or corrupt frames are skipped so a bad ``preview`` field does not
    exclude the game from ``load_game_list()`` / ``get_games_summary()``.
    """
    images: list = []
    if not isinstance(preview_raw, list):
        _log_bad_preview_once(
            game_label,
            f"invalid-type:{type(preview_raw).__name__}",
            "Invalid preview for %s (expected list, got %s); using blank frame",
            game_label,
            type(preview_raw).__name__,
        )
    else:
        for img_b64 in preview_raw:
            try:
                if not isinstance(img_b64, str):
                    continue
                chunk = img_b64.strip().replace("\n", "").replace("\r", "")
                pad = (-len(chunk)) % 4
                if pad:
                    chunk += "=" * pad
                img_data = base64.b64decode(chunk, validate=False)
                img = Image.open(io.BytesIO(img_data))
                img = img.convert("RGB").resize((128, 128), Image.LANCZOS)
                image = Image.new("RGB", (128, 160), (0, 0, 0))
                image.paste(img, (0, 0))
                images.append(bytearray(image.tobytes()))
            except Exception as e:
                _log_bad_preview_once(
                    game_label,
                    type(e).__name__,
                    "Skipping bad preview frame for %s: %s",
                    game_label,
                    e,
                )
    if not images:
        blank = Image.new("RGB", (128, 160), (0, 0, 0))
        return [bytearray(blank.tobytes())]
    return images


def _log_bad_preview_once(
    game_label: str,
    reason_key: str,
    message: str,
    *args,
) -> None:
    """Warn once for repeated bad preview decode failures, then keep repeats at debug."""
    key = (str(game_label), str(reason_key))
    with _bad_preview_warning_lock:
        first_seen = key not in _bad_preview_warning_keys
        if first_seen:
            _bad_preview_warning_keys.add(key)
    if first_seen:
        _log.warning(message, *args)
    else:
        _log.debug(message, *args)


def generate_placeholder_preview(game_name: str, status_hint: str) -> list:
    """
    Generate a 128x160 black placeholder frame with game name and status text.

    Returns a single-frame list matching the format of _decode_game_preview_frames().
    """
    from PIL import ImageDraw, ImageFont
    canvas = Image.new("RGB", (128, 160), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    # Try to load a small font; fall back to PIL default if unavailable
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
    # Truncate long names to fit 128px width
    name = game_name[:18] if len(game_name) > 18 else game_name
    name_bbox = draw.textbbox((0, 0), name, font=font)
    status_bbox = draw.textbbox((0, 0), status_hint, font=font)
    line_gap = 4
    name_height = name_bbox[3] - name_bbox[1]
    status_height = status_bbox[3] - status_bbox[1]
    block_height = name_height + line_gap + status_height
    y = max(0, (128 - block_height) // 2)
    draw.text((4, y - name_bbox[1]), name, fill=(200, 200, 200), font=font)
    draw.text((4, y + name_height + line_gap - status_bbox[1]), status_hint, fill=(120, 120, 120), font=font)
    return [bytearray(canvas.tobytes())]


def _is_blank_frame(frame: bytearray) -> bool:
    """Return True if the frame contains only black pixels (all zeros)."""
    return all(b == 0 for b in frame)


def get_local_game_version(gameid: str) -> str:
    """Read local game version from backend metadata."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        return ""
    return str(read_app_metadata(gameid).get("version") or "").strip()


def _game_metadata_from_download_info(game_id: str, data: dict) -> dict:
    cover = str(data.get("main_cover") or data.get("cover") or "").strip()
    preview_urls = data.get("preview_urls") or data.get("preview") or []
    if not isinstance(preview_urls, list):
        preview_urls = []
    if cover and not preview_urls:
        preview_urls = [cover]
    return {
        "id": str(data.get("game_id") or data.get("id") or game_id),
        "type": "game",
        "version": str(data.get("version") or ""),
        "name": str(data.get("game_name") or data.get("name") or ""),
        "preview_urls": preview_urls,
        "download_url": str(data.get("game_download_url") or ""),
        "download_md5": str(data.get("game_download_md5") or ""),
    }


def compare_game_versions(left: str, right: str) -> int:
    """
    Compare dotted numeric game versions (e.g. 1.2.3).

    Returns -1, 0, or 1. Non-numeric versions fall back to string ordering.
    """
    a = str(left or "").strip()
    b = str(right or "").strip()
    if a == b:
        return 0

    def _parts(value: str) -> tuple[int, ...] | None:
        out: list[int] = []
        for piece in value.split("."):
            piece = piece.strip()
            if not piece:
                continue
            if not piece.isdigit():
                return None
            out.append(int(piece))
        return tuple(out)

    ap = _parts(a)
    bp = _parts(b)
    if ap is not None and bp is not None:
        width = max(len(ap), len(bp))
        ap = ap + (0,) * (width - len(ap))
        bp = bp + (0,) * (width - len(bp))
        if ap > bp:
            return 1
        if ap < bp:
            return -1
        return 0
    return (a > b) - (a < b)


def resolve_game_version_for_sync(game_id: str, remote_version: str = "") -> str:
    """Prefer the higher of local and remote version strings for Supabase publish."""
    local = get_local_game_version(game_id)
    remote = str(remote_version or "").strip()
    if not remote:
        return local
    if not local:
        return remote
    return local if compare_game_versions(local, remote) >= 0 else remote


def local_game_version_matches(gameid: str, remote_version: str) -> bool:
    """Return True when local game exists and version is >= remote target."""
    expected = str(remote_version or "").strip()
    if not expected:
        return False
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        return False
    local = get_local_game_version(gameid)
    if not local:
        return False
    return compare_game_versions(local, expected) >= 0


def _download_game_file(url: str, md5: str, game_id: str, metadata: dict | None = None) -> bool:
    """
    Download and extract game .tar.gz file; verify MD5.
    Returns True on success, False on failure.
    """
    if not url.endswith(".tar.gz"):
        _log.error("game: invalid file type url=%s (expected .tar.gz)", url)
        return False

    try:
        os.makedirs("downloads", exist_ok=True)
        file_name = url.split("/")[-1]
        download_path = os.path.join("downloads", file_name)

        # Download with wget
        try:
            subprocess.run(
                ["wget", "--read-timeout=10", "-O", download_path, url],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            _log.error("game: wget failed game_id=%s: %s", game_id, e)
            return False

        if not os.path.isfile(download_path):
            _log.error("game: download file not found game_id=%s", game_id)
            return False

        # Verify MD5
        try:
            result = subprocess.run(
                ["md5sum", download_path],
                capture_output=True,
                text=True,
                check=True,
            )
            downloaded_md5 = result.stdout.split()[0]
            if downloaded_md5 != md5:
                _log.error(
                    "game: MD5 mismatch game_id=%s expected=%s got=%s",
                    game_id,
                    md5,
                    downloaded_md5,
                )
                os.remove(download_path)
                return False
        except subprocess.CalledProcessError as e:
            _log.error("game: MD5 check failed game_id=%s: %s", game_id, e)
            if os.path.isfile(download_path):
                os.remove(download_path)
            return False

        # Extract tarball into apps/<game_id>, independent of archive naming.
        try:
            install_app_tarball(download_path, game_id)
        except Exception as e:
            _log.error("game: extraction failed game_id=%s: %s", game_id, e)
            if os.path.isfile(download_path):
                os.remove(download_path)
            return False

        # Clean up downloaded file
        if os.path.isfile(download_path):
            os.remove(download_path)

        # Clean up macOS metadata files that may have been extracted
        apps_dir = os.path.join(os.getcwd(), "apps")
        try:
            for item in os.listdir(apps_dir):
                if item.startswith("._"):
                    macos_file = os.path.join(apps_dir, item)
                    if os.path.isfile(macos_file):
                        os.remove(macos_file)
                        _log.debug("game: removed macOS metadata file %s", item)
                    elif os.path.isdir(macos_file):
                        shutil.rmtree(macos_file)
                        _log.debug("game: removed macOS metadata dir %s", item)
        except Exception as e:
            _log.warning("game: error cleaning macOS metadata for game_id=%s: %s", game_id, e)

        # Set up venv
        if isinstance(metadata, dict):
            write_app_metadata(game_id, metadata)
        if not ensure_app_venv(game_id):
            _log.error("game: venv setup failed game_id=%s", game_id)
            return False

        _log.info("game: download and setup complete game_id=%s", game_id)
        return True

    except Exception as e:
        _log.error("game: download error game_id=%s: %s", game_id, e)
        return False


def ensure_game_downloaded(gameid: str, remote_version: str = "") -> bool:
    """Ensure local game exists and is at least remote_version when provided."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    expected_version = str(remote_version or "").strip()
    normalized_gameid = str(gameid or "").strip()
    local_version = ""
    if os.path.isdir(game_path):
        if not expected_version:
            if normalized_gameid == "pico8":
                _sync_pico8_favourites_if_configured()
            return True
        local_version = get_local_game_version(gameid)
        if compare_game_versions(local_version, expected_version) >= 0:
            if local_version != expected_version:
                _log.info(
                    "Game %s local version %s >= target %s; treating as up to date",
                    gameid,
                    local_version or "(empty)",
                    expected_version,
                )
            if normalized_gameid == "pico8":
                _sync_pico8_favourites_if_configured()
            return True
        _log.info(
            "Game %s local version %s < target %s; downloading update",
            gameid,
            local_version or "(empty)",
            expected_version,
        )
    try:
        response = retry_with_backoff(
            lambda: requests.get(
                "https://api.dartsnut.com/v1/mobile/game/get-download-info",
                params={"id": gameid, "version": expected_version},
                headers=build_api_headers(),
                timeout=(5, 30),
            ),
            succeeded=lambda r: getattr(r, "status_code", None) == 200,
            label=f"get-download-info {gameid}",
        )
        if response is not None and response.status_code == 200:
            data = response.json().get("data")
            if data:
                metadata = _game_metadata_from_download_info(gameid, data)
                backend_id = metadata.get("id") or gameid
                if backend_id != gameid:
                    _log.error(
                        "game: backend id mismatch requested=%s backend=%s",
                        gameid,
                        backend_id,
                    )
                    return False
                u = data.get("game_download_url")
                m = data.get("game_download_md5")
                if u and m:
                    success = retry_with_backoff(
                        lambda: _download_game_file(u, m, gameid, metadata),
                        succeeded=bool,
                        label=f"download-game {gameid}",
                    )
                    if not success:
                        _log.error("game: download failed after retries game_id=%s", gameid)
                        return False
                else:
                    _log.error("game: missing download URL or MD5 game_id=%s", gameid)
                    return False
        else:
            _log.warning(
                "Failed to get download info for game %s: HTTP %s",
                gameid,
                getattr(response, "status_code", "n/a"),
            )
            return False
    except Exception as e:
        _log.error("Error fetching game download info for %s: %s", gameid, e)
        return False

    if expected_version:
        local = get_local_game_version(gameid)
        ok = compare_game_versions(local, expected_version) >= 0
    else:
        ok = os.path.isdir(game_path)
    if ok and normalized_gameid == "pico8":
        _sync_pico8_favourites_if_configured()
    return ok


def download_game_async(
    game_id: str,
    expected_version: str = "",
    on_success=None,
    on_failure=None,
) -> bool:
    """
    Download game in background thread; return True if download was started.

    Args:
        game_id: Game identifier
        expected_version: Target version to download
        on_success: Callback(game_id) called after successful download
        on_failure: Callback(game_id, error_msg) called on failure

    Returns:
        True if background download was started, False if already in progress
    """
    if not game_id:
        return False

    with _game_download_lock:
        if game_id in _game_background_download_inflight:
            _log.debug(
                "game: background download already in progress game_id=%s (skipped duplicate)",
                game_id,
            )
            return False
        _game_background_download_inflight.add(game_id)

    def worker():
        try:
            _log.info("game: background download started game_id=%s", game_id)
            success = ensure_game_downloaded(game_id, expected_version)
            if success:
                _log.info("game: background download finished game_id=%s", game_id)
                if on_success is not None:
                    try:
                        on_success(game_id)
                    except Exception as e:
                        _log.warning(
                            "game: on_success callback error game_id=%s: %s",
                            game_id,
                            e,
                        )
            else:
                _log.warning("game: background download failed game_id=%s", game_id)
                if on_failure is not None:
                    try:
                        on_failure(game_id, "Download failed")
                    except Exception as e:
                        _log.warning(
                            "game: on_failure callback error game_id=%s: %s",
                            game_id,
                            e,
                        )
        except Exception as e:
            _log.error("game: background download error game_id=%s: %s", game_id, e)
            if on_failure is not None:
                try:
                    on_failure(game_id, str(e))
                except Exception:
                    pass
        finally:
            with _game_download_lock:
                _game_background_download_inflight.discard(game_id)

    threading.Thread(target=worker, daemon=True).start()
    return True


def start_game_process(gameid: str) -> dict:
    """Start game process and return its process/shared-memory runtime state."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        ensure_game_downloaded(gameid)
    if not os.path.isdir(game_path):
        return None
    metadata = read_app_metadata(gameid)
    if metadata.get("type") != "game":
        _log.error("Refusing to start %s: missing or invalid game sidecar", gameid)
        return None
    if not ensure_app_venv(gameid):
        _log.error("Failed to set up virtualenv for game %s", gameid)
        return None
    if str(gameid or "").strip() == "pico8":
        _sync_pico8_favourites_if_configured()
    shm_name = "game_shm"
    shm_size = 128 * 160 * 3 + 1
    try:
        existing = shared_memory.SharedMemory(name=shm_name)
        existing.close()
        shared_memory.SharedMemory(name=shm_name).unlink()
    except (FileNotFoundError, FileExistsError):
        pass
    try:
        shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
        loading_image = assets.create_loading_image()
        img_bytes = loading_image.tobytes()
        shm.buf[1 : 1 + len(img_bytes)] = img_bytes
        # The first byte is the producer/consumer frame handshake. Standard
        # games start with no subprocess frame available; Pico-8 retains its
        # legacy startup sequence and consumes the preloaded loading frame.
        shm.buf[0] = 0 if gameid == "pico8" else 1
        command = app_python_command(gameid, "main.py")
        command.extend(["--shm", shm_name])
        command.extend(["--data-store", get_user_data_store_path(gameid)])
        process = subprocess.Popen(
            command,
            cwd=app_dir(gameid),
            **subprocess_launch_kwargs(),
        )
        try:
            start_game_tracking(gameid)
        except Exception as e:
            _log.warning("Failed to start game tracking: %s", e)
        _log.info(
            "game process started game_id=%s pid=%s",
            gameid,
            getattr(process, "pid", None),
        )
        return {
            "process": process,
            "shm": shm,
            "game_id": gameid,
            "loading": True,
            "pico8_first_frame_seen": False,
        }
    except Exception as e:
        _log.error("Error starting game %s: %s", gameid, e)
        return None


def term_game_process(g: dict) -> None:
    """Terminate game process and clean up; clear game dict. Returns None (caller should set game = None)."""
    if g is None:
        return
    try:
        try:
            stop_game_tracking()
        except Exception as e:
            _log.warning("Failed to stop game tracking: %s", e)
        if g.get("process") and g["process"].poll() is None:
            terminate_process_group(g["process"].pid)
        if g.get("shm"):
            g["shm"].close()
            g["shm"].unlink()
        g.clear()
    except Exception as e:
        _log.error("Error terminating game: %s", e)
    return None


def load_game_list() -> list:
    """Load sidecar-declared games from apps directory with previews decoded."""
    game_list = []
    apps_dir = os.path.join(os.getcwd(), "apps")
    for name in os.listdir(apps_dir):
        path = os.path.join(apps_dir, name)
        if not os.path.isdir(path):
            continue
        if not os.path.isfile(os.path.join(path, "main.py")):
            continue
        try:
            metadata = read_app_metadata(name)
            if metadata.get("type") != "game":
                continue
            backend_id = str(metadata.get("id") or "").strip()
            if not backend_id:
                continue
            conf = {
                "id": backend_id,
                "type": "game",
                "name": str(metadata.get("name") or backend_id),
                "version": str(metadata.get("version") or ""),
            }
            # Default status for on-device list; more specific statuses (e.g. playing,
            # downloading) can be layered on top where appropriate.
            conf.setdefault("status", "ready")
            preview = []

            # Preview always comes from backend/cache; packaged conf.json preview is legacy.
            if backend_id:
                _ensure_worker_started()
                cached = _preview_cache.get_cached_preview(backend_id)
                if cached:
                    preview = cached
                    if _preview_cache.is_cache_expired(backend_id):
                        _validation_worker.submit(backend_id, priority=VALIDATE_EXPIRED, callback=_on_preview_updated)
                else:
                    preview = generate_placeholder_preview(conf["name"], "Loading Preview")
                    _validation_worker.submit(backend_id, priority=FETCH_MISSING, callback=_on_preview_updated)
            else:
                preview = generate_placeholder_preview(conf["name"], "Preview unavailable")

            conf["preview"] = preview
            game_list.append(conf)
        except Exception as e:
            _log.warning("Error loading game metadata for %s: %s", name, e)
    with _game_list_cache_lock:
        _game_list_cache.clear()
        _game_list_cache.extend(game_list)
    return game_list


def local_game_index() -> dict[str, str]:
    """Map locally installed backend game ids to their app folder paths."""
    games: dict[str, str] = {}
    apps_dir = os.path.abspath(os.path.join(os.getcwd(), "apps"))
    try:
        names = os.listdir(apps_dir)
    except FileNotFoundError:
        return games
    for name in names:
        path = os.path.join(apps_dir, name)
        if not os.path.isdir(path):
            continue
        if not os.path.isfile(os.path.join(path, "main.py")):
            continue
        try:
            metadata = read_app_metadata(name)
            if metadata.get("type") != "game":
                continue
            game_id = str(metadata.get("id") or "").strip()
            if game_id:
                games[game_id] = os.path.abspath(path)
        except Exception as e:
            _log.warning("Error indexing game config for %s: %s", name, e)
    return games


def remove_local_game_folder(game_id: str) -> bool:
    """Delete a locally installed game folder by game id."""
    gid = str(game_id or "").strip()
    if not gid:
        return False
    folder = local_game_index().get(gid)
    if not folder:
        return False
    apps_dir = os.path.abspath(os.path.join(os.getcwd(), "apps"))
    folder = os.path.abspath(folder)
    try:
        if os.path.commonpath([apps_dir, folder]) != apps_dir:
            return False
    except ValueError:
        return False
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder)
    return True


def load_menu_game_list(ctx) -> list:
    """
    Games for the on-device picker: local entries marked ready, sorted by
    playtime descending then name.
    """
    all_games = load_game_list()
    filtered = [
        c
        for c in all_games
        if str(c.get("status", "ready")).strip().lower() == "ready"
    ]

    playtimes = _load_user_data().get("game_playtimes", {})

    def _sort_key(conf: dict):
        gid = str(conf.get("id"))
        pt = playtimes.get(gid, 0)
        try:
            pt = int(pt)
        except (TypeError, ValueError):
            pt = 0
        name = (conf.get("name") or "").lower()
        return (-pt, name)

    return sorted(filtered, key=_sort_key)


def refresh_menu_game_list_if_requested(ctx) -> None:
    """Rebuild ctx.game_list after remote sync when reload_game_menu is set."""
    if not getattr(ctx, "reload_game_menu", False):
        return
    loader = getattr(ctx, "load_game_list", None)
    if not loader:
        ctx.reload_game_menu = False
        return
    ctx.game_list = loader()
    ctx.reload_game_menu = False
    n = len(ctx.game_list)
    if n == 0:
        ctx.game_index = 0
        ctx.game_preview_index = 0
        return
    if ctx.game_index < 0:
        ctx.game_index = 0
    elif ctx.game_index >= n:
        ctx.game_index = n - 1
    previews = ctx.game_list[ctx.game_index].get("preview") or []
    plen = len(previews)
    if plen == 0:
        ctx.game_preview_index = 0
    else:
        if ctx.game_preview_index < 0:
            ctx.game_preview_index = 0
        elif ctx.game_preview_index >= plen:
            ctx.game_preview_index = plen - 1


def get_games_summary() -> list:
    """
    Build a lightweight games summary list for configuration / syncing.

    Each entry has the shape:
    - id: game identifier from backend metadata
    - version: version string from backend metadata (or empty string if missing)
    - status: one of \"ready\", \"downloading\", \"playing\". This helper only
      sets \"ready\" based on local presence; higher layers can refine status
      when they have download/runtime context.
    """
    summary: list = []
    for conf in load_game_list():
        game_id = conf.get("id")
        if not game_id:
            continue
        summary.append(
            {
                "id": game_id,
                "version": conf.get("version", ""),
                "status": conf.get("status", "ready"),
            }
        )
    return summary
