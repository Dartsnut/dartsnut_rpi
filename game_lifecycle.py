"""Game lifecycle: start/term game process, load game list."""
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
from core.app_env import ensure_app_venv
from core.retry import retry_with_backoff
from python_websocket.user_data_operations import (
    start_game_tracking,
    stop_game_tracking,
    _load_user_data,
)
from widget_lifecycle import download_app
import assets
from PIL import Image

_log = logging.getLogger(__name__)

# Track in-flight game downloads (by game_id) to prevent duplicate concurrent downloads
_game_background_download_inflight = set()
_game_download_lock = threading.Lock()


def _decode_game_preview_frames(preview_raw, game_label: str) -> list:
    """
    Decode base64-encoded preview images into RGB buffers for the game picker.

    Malformed or corrupt frames are skipped so a bad ``preview`` field does not
    exclude the game from ``load_game_list()`` / ``get_games_summary()``.
    """
    images: list = []
    if not isinstance(preview_raw, list):
        _log.warning(
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
                _log.warning("Skipping bad preview frame for %s: %s", game_label, e)
    if not images:
        blank = Image.new("RGB", (128, 160), (0, 0, 0))
        return [bytearray(blank.tobytes())]
    return images


def get_local_game_version(gameid: str) -> str:
    """Read local game version from ./apps/<gameid>/conf.json."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        return ""
    conf_path = os.path.join(game_path, "conf.json")
    if not os.path.isfile(conf_path):
        return ""
    try:
        with open(conf_path, "r") as f:
            conf = json.load(f)
        return str(conf.get("version") or "").strip()
    except Exception:
        return ""


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


def ensure_game_downloaded(gameid: str, remote_version: str = "") -> bool:
    """Ensure local game exists and is at least remote_version when provided."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    expected_version = str(remote_version or "").strip()
    if os.path.isdir(game_path):
        if not expected_version:
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
                f"https://api.dartsnut.com/v1/mobile/game/get-download-info?id={gameid}",
                timeout=(5, 30),
            ),
            succeeded=lambda r: getattr(r, "status_code", None) == 200,
            label=f"get-download-info {gameid}",
        )
        if response is not None and response.status_code == 200:
            data = response.json().get("data")
            if data:
                u = data.get("game_download_url")
                m = data.get("game_download_md5")
                if u and m:
                    download_app(u, m)
        else:
            _log.warning(
                "Failed to get download info for game %s: HTTP %s",
                gameid,
                getattr(response, "status_code", "n/a"),
            )
    except Exception as e:
        _log.warning("Error fetching game download info: %s", e)
    if expected_version:
        local = get_local_game_version(gameid)
        return compare_game_versions(local, expected_version) >= 0
    return os.path.isdir(game_path)


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
    """Start game process and return game dict (process, shm, game_id, launched, pico8_first_frame_seen) or None."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        ensure_game_downloaded(gameid)
    if not os.path.isdir(game_path):
        return None
    if not ensure_app_venv(gameid):
        _log.error("Failed to set up virtualenv for game %s", gameid)
        return None
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
        shm.buf[0] = 0
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
            "launched": False,
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
    """Load game list from apps directory with preview images decoded."""
    game_list = []
    apps_dir = os.path.join(os.getcwd(), "apps")
    for name in os.listdir(apps_dir):
        path = os.path.join(apps_dir, name)
        if not os.path.isdir(path):
            continue
        conf_path = os.path.join(path, "conf.json")
        if not os.path.isfile(conf_path):
            continue
        try:
            with open(conf_path, "r") as f:
                conf = json.load(f)
            if conf.get("type") != "game":
                continue
            # Ensure core fields exist for downstream consumers.
            conf_id = conf.get("id", name)
            conf_version = conf.get("version", "")
            conf["id"] = conf_id
            conf["version"] = conf_version
            # Default status for on-device list; more specific statuses (e.g. playing,
            # downloading) can be layered on top where appropriate.
            conf.setdefault("status", "ready")
            if "preview" in conf:
                conf["preview"] = _decode_game_preview_frames(conf.get("preview"), name)
            game_list.append(conf)
        except Exception as e:
            _log.warning("Error loading game config for %s: %s", name, e)
    return game_list


def local_game_index() -> dict[str, str]:
    """Map locally installed game ids to their app folder paths."""
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
        conf_path = os.path.join(path, "conf.json")
        if not os.path.isfile(conf_path):
            continue
        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                conf = json.load(f)
            if conf.get("type") != "game":
                continue
            game_id = str(conf.get("id") or name).strip()
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
    - id: game identifier from apps/{game}/conf.json
    - version: version string from conf.json (or empty string if missing)
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
