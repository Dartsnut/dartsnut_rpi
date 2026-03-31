"""Game lifecycle: start/term game process, load game list."""
import base64
import io
import json
import os
import signal
from multiprocessing import shared_memory
import subprocess
import requests

from core.helpers import set_pdeathsig, get_user_data_store_path
from python_websocket.user_data_operations import (
    start_game_tracking,
    stop_game_tracking,
    _load_user_data,
)
from widget_lifecycle import download_app
import assets
from PIL import Image


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


def local_game_version_matches(gameid: str, remote_version: str) -> bool:
    """Return True only when local game exists and versions match exactly."""
    expected = str(remote_version or "").strip()
    if not expected:
        return False
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        return False
    return get_local_game_version(gameid) == expected


def ensure_game_downloaded(gameid: str, remote_version: str = "") -> bool:
    """Ensure local game exists and matches remote_version when provided."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    expected_version = str(remote_version or "").strip()
    if os.path.isdir(game_path):
        if not expected_version:
            return True
        local_version = get_local_game_version(gameid)
        if local_version == expected_version:
            return True
        print(
            f"Game {gameid} local version {local_version or '(empty)'} does not match target "
            f"{expected_version}; downloading update"
        )
    try:
        response = requests.get(
            f"https://api.dartsnut.com/v1/mobile/game/get-download-info?id={gameid}"
        )
        if response.status_code == 200:
            data = response.json().get("data")
            if data:
                u = data.get("game_download_url")
                m = data.get("game_download_md5")
                if u and m:
                    download_app(u, m)
        else:
            print(f"Failed to get download info for game {gameid}: {response.status_code}")
    except Exception as e:
        print(f"Error fetching game download info: {e}")
    if expected_version:
        return get_local_game_version(gameid) == expected_version
    return os.path.isdir(game_path)


def start_game_process(gameid: str) -> dict:
    """Start game process and return game dict (process, shm, game_id, launched, pico8_first_frame_seen) or None."""
    game_path = os.path.join(os.getcwd(), "apps", gameid)
    if not os.path.isdir(game_path):
        ensure_game_downloaded(gameid)
    if not os.path.isdir(game_path):
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
        command = [
            os.path.join(os.getcwd(), "venv0/bin/python"),
            os.path.join(os.getcwd(), "apps/", gameid, "main.py"),
        ]
        command.extend(["--shm", shm_name])
        command.extend(["--data-store", get_user_data_store_path(gameid)])
        process = subprocess.Popen(
            command,
            cwd=os.path.join("./apps/", gameid),
            preexec_fn=set_pdeathsig,
        )
        try:
            start_game_tracking(gameid)
        except Exception as e:
            print(f"Warning: Failed to start game tracking: {e}")
        return {
            "process": process,
            "shm": shm,
            "game_id": gameid,
            "launched": False,
            "pico8_first_frame_seen": False,
        }
    except Exception as e:
        print(f"Error starting game {gameid}: {e}")
        return None


def term_game_process(g: dict) -> None:
    """Terminate game process and clean up; clear game dict. Returns None (caller should set game = None)."""
    if g is None:
        return
    try:
        try:
            stop_game_tracking()
        except Exception as e:
            print(f"Warning: Failed to stop game tracking: {e}")
        if g.get("process") and g["process"].poll() is None:
            os.kill(g["process"].pid, signal.SIGCONT)
            os.kill(g["process"].pid, signal.SIGKILL)
        if g.get("shm"):
            g["shm"].close()
            g["shm"].unlink()
        g.clear()
    except Exception as e:
        print(f"Error terminating game: {e}")
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
                images = []
                for img_b64 in conf["preview"]:
                    img_data = base64.b64decode(img_b64)
                    img = Image.open(io.BytesIO(img_data))
                    img = img.convert("RGB").resize((128, 128), Image.LANCZOS)
                    image = Image.new("RGB", (128, 160), (0, 0, 0))
                    image.paste(img, (0, 0))
                    images.append(bytearray(image.tobytes()))
                conf["preview"] = images
            game_list.append(conf)
        except Exception as e:
            print(f"Error loading game config for {name}: {e}")
    return game_list


def load_menu_game_list(ctx) -> list:
    """
    Games for the on-device picker: intersect with remote-ready ids when known,
    else local entries marked ready; sort by playtime descending then name.
    """
    all_games = load_game_list()
    ready_ids = getattr(ctx, "remote_menu_ready_game_ids", None)
    if ready_ids is None:
        filtered = [
            c
            for c in all_games
            if str(c.get("status", "ready")).strip().lower() == "ready"
        ]
    elif len(ready_ids) == 0:
        filtered = []
    else:
        filtered = [c for c in all_games if str(c.get("id")) in ready_ids]

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
