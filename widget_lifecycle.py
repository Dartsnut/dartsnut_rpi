"""Widget lifecycle: page/widget process start, term, restart, update checks."""
from __future__ import annotations

import copy
import json
import logging
import os
import signal
import threading
import time
from multiprocessing import shared_memory
import subprocess
import base64
import tempfile
import requests
from PIL import Image

from core.helpers import set_pdeathsig, get_user_data_store_path
from domain.app_context import AppContext

_log = logging.getLogger(__name__)

# Throttling and update tracking (used by check_page_widget_updates and kill_widget_if_page_inactive)
widget_update_checks = {}
widgets_updated = set()
_missing_widget_download_attempts = {}
# One background download per widget id (missing-app bootstrap + update check can both fire).
_widget_background_download_inflight = set()


def _get_flattened_data_compat(img):
    """Backwards-compatible pixel flattener for PIL Image."""
    if hasattr(img, "get_flattened_data"):
        try:
            return img.get_flattened_data()
        except Exception:
            pass
    if hasattr(img, "getdata"):
        try:
            data_iter = iter(img.getdata())
            first = next(data_iter, None)
            if first is None:
                return []
            if isinstance(first, (tuple, list)):
                flat = list(first)
                for px in data_iter:
                    flat.extend(px)
                return flat
            return [first, *list(data_iter)]
        except Exception:
            pass
    try:
        return list(img.tobytes())
    except Exception:
        return []


def check_widget_ready(widget_frame) -> bool:
    """True if top or bottom row of widget frame has any non-black pixel."""
    if widget_frame is None:
        return False
    width, height = widget_frame.size
    top_row = widget_frame.crop((0, 0, width, 1))
    top_pixels_flat = _get_flattened_data_compat(top_row)
    if any(value != 0 for value in top_pixels_flat):
        return True
    if height > 1:
        bottom_row = widget_frame.crop((0, height - 1, width, height))
        bottom_pixels_flat = _get_flattened_data_compat(bottom_row)
        if any(value != 0 for value in bottom_pixels_flat):
            return True
    return False


def process_widget_fields(widget_id: str, widget_fields_parameter: dict) -> dict:
    """Process widget fields (e.g. decode image base64) using conf.json."""
    params = widget_fields_parameter.copy()
    conf_path = os.path.join(os.getcwd(), "apps", widget_id, "conf.json")
    with open(conf_path, "r") as f:
        conf = json.load(f)
        for field in conf["fields"]:
            if field["type"] == "image" and params.get(field["id"]) is not None:
                file = params[field["id"]]["image"]
                if len(file) > 500:
                    file_data = base64.b64decode(file)
                    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                        tmp_file.write(file_data)
                        params[field["id"]]["image"] = tmp_file.name
    return params


def check_and_update_widget_version(widget_id: str):
    """Return (needs_update: bool, download_info: dict or None)."""
    try:
        conf_path = os.path.join(os.getcwd(), "apps", widget_id, "conf.json")
        local_version = None
        if os.path.isfile(conf_path):
            try:
                with open(conf_path, "r") as f:
                    local_version = json.load(f).get("version")
            except Exception as e:
                _log.warning("Error reading conf.json for widget %s: %s", widget_id, e)
        try:
            response = requests.get(
                f"https://api.dartsnut.com/v1/mobile/widget/get-download-info?id={widget_id}"
            )
            if response.status_code != 200:
                _log.warning(
                    "Failed to get download info for widget %s: HTTP %s",
                    widget_id,
                    response.status_code,
                )
                return (False, None)
            download_info = response.json().get("data")
            if download_info is None:
                return (False, None)
            api_version = download_info.get("version")
            if local_version is None:
                return (True, download_info)
            if api_version is None:
                return (False, None)
            if local_version != api_version:
                return (True, download_info)
            return (False, download_info)
        except Exception as e:
            _log.warning("Error fetching widget download info for %s: %s", widget_id, e)
            return (False, None)
    except Exception as e:
        _log.warning("Error checking widget version for %s: %s", widget_id, e)
        return (False, None)


def download_app(url: str, md5: str) -> bool:
    """Download and extract .tar.gz app; verify MD5. Return True on success."""
    try:
        if not url.endswith(".tar.gz"):
            return False
        os.makedirs("downloads", exist_ok=True)
        file_name = url.split("/")[-1]
        download_path = os.path.join("downloads", file_name)
        try:
            subprocess.run(
                ["wget", "--read-timeout=10", "-O", download_path, url], check=True
            )
        except subprocess.CalledProcessError:
            return False
        if not os.path.isfile(download_path):
            return False
        try:
            result = subprocess.run(
                ["md5sum", download_path], capture_output=True, text=True, check=True
            )
            if result.stdout.split()[0] != md5:
                os.remove(download_path)
                return False
        except subprocess.CalledProcessError:
            os.remove(download_path)
            return False
        try:
            subprocess.run(
                ["tar", "-xzf", download_path, "-C", os.path.join(os.getcwd(), "apps")],
                check=True,
            )
        except subprocess.CalledProcessError:
            return False
        return True
    except Exception as e:
        _log.error("Error downloading app: %s", e)
        return False


def _kill_widget_process(widget_entry: dict, widget_id: str, reason: str = "") -> None:
    """Kill widget process and clean up shared memory."""
    try:
        process = widget_entry.get("process")
        if process and process.poll() is None:
            _log.info(
                "widget: killing process id=%s%s",
                widget_id,
                f" ({reason})" if reason else "",
            )
            os.kill(process.pid, signal.SIGCONT)
            os.kill(process.pid, signal.SIGKILL)
        shm = widget_entry.get("shm")
        if shm:
            try:
                shm.close()
                shm.unlink()
            except Exception as e:
                _log.warning("Error cleaning up shared memory for widget %s: %s", widget_id, e)
        widget_entry["process"] = None
        widget_entry["shm"] = None
        widget_entry["launched"] = False
    except Exception as e:
        _log.error("Error killing widget %s process: %s", widget_id, e)


def kill_widget_if_page_inactive(widget_id: str, get_context) -> None:
    """Called after widget update download. Kill process if page not active else mark for restart."""
    ctx = get_context()
    if ctx is None or ctx.state_str != "widget" or ctx.pages is None:
        return
    for page_idx, page in enumerate(ctx.pages):
        for widget_entry in page.get("widgets", []):
            w = widget_entry.get("widget")
            if w and w.get("id") == widget_id:
                if page_idx != ctx.page_index:
                    _kill_widget_process(
                        widget_entry, widget_id, "page not active, update complete"
                    )
                else:
                    widgets_updated.add(widget_id)
                    _log.info(
                        "widget: %s update complete (active page; restart on suspend)",
                        widget_id,
                    )
                return


def download_widget_async(
    widget_id: str, url: str, md5: str, get_context=None, on_complete=None
) -> None:
    """Download widget in background; on success call kill_widget_if_page_inactive."""
    if widget_id in _widget_background_download_inflight:
        _log.debug(
            "widget: background download already in progress id=%s (skipped duplicate)",
            widget_id,
        )
        return
    _widget_background_download_inflight.add(widget_id)

    def worker():
        try:
            _log.info("widget: background download started id=%s", widget_id)
            if download_app(url, md5):
                _log.info("widget: background download finished id=%s", widget_id)
                if get_context is not None:
                    kill_widget_if_page_inactive(widget_id, get_context)
            else:
                _log.warning("widget: background download failed id=%s", widget_id)
        except Exception as e:
            _log.error("widget: background download error id=%s: %s", widget_id, e)
        finally:
            _widget_background_download_inflight.discard(widget_id)
            if on_complete is not None:
                try:
                    on_complete()
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()


def _request_missing_widget_download(widget_id: str, *, force: bool = False) -> None:
    """Ensure missing widget app download is attempted in background with throttling."""
    if not widget_id or widget_id == "0":
        return
    if os.path.isdir(os.path.join(os.getcwd(), "apps", widget_id)):
        return
    now = time.time()
    last_attempt = _missing_widget_download_attempts.get(widget_id, 0.0)
    if not force and (now - last_attempt) < 30:
        return
    _missing_widget_download_attempts[widget_id] = now

    try:
        needs_update, download_info = check_and_update_widget_version(widget_id)
    except Exception as e:
        _log.warning("widget: missing-widget download check failed id=%s: %s", widget_id, e)
        return

    if not needs_update or not isinstance(download_info, dict):
        return

    url = download_info.get("widget_download_url")
    md5 = download_info.get("widget_download_md5")
    if not url or not md5:
        return

    download_widget_async(widget_id, url, md5, get_context=None)


def restart_widget_process(
    widget_entry: dict, page: dict, widget_index: int
) -> None:
    """Restart a widget process (e.g. after update)."""
    widget = widget_entry.get("widget")
    if not widget:
        return
    widget_id = widget.get("id")
    if widget_id == "0":
        return
    widget_path = os.path.join(os.getcwd(), "apps", widget_id)
    if not os.path.isdir(widget_path):
        _request_missing_widget_download(widget_id)
        return
    try:
        page_uuid = page["uuid"]
        shm_name = f"widget_{page_uuid}_{widget_index}_shm"
        shm_size = (widget["position"][2] - widget["position"][0] + 1) * (
            widget["position"][3] - widget["position"][1] + 1
        ) * 3 + 1
        for name in [shm_name]:
            try:
                existing = shared_memory.SharedMemory(name=name)
                existing.close()
                shared_memory.SharedMemory(name=name).unlink()
            except FileNotFoundError:
                pass
            except FileExistsError:
                shared_memory.SharedMemory(name=name).unlink()
        shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
        shm.buf[0] = 1
        command = [
            os.path.join(os.getcwd(), "venv0/bin/python"),
            os.path.join(os.getcwd(), "apps/", widget_id, "main.py"),
        ]
        command.extend(
            ["--params", json.dumps(process_widget_fields(widget_id, widget["fields"]))]
        )
        command.extend(["--shm", shm_name])
        command.extend(["--data-store", get_user_data_store_path(widget_id)])
        process = subprocess.Popen(
            command,
            cwd=os.path.join("./apps/", widget_id),
            preexec_fn=set_pdeathsig,
        )
        widget_entry["process"] = process
        widget_entry["shm"] = shm
        widget_entry["launched"] = False
        widget_entry["has_small_widget"] = None
        _log.info("widget: restarted process id=%s pid=%s", widget_id, process.pid)
    except Exception as e:
        _log.error("Error restarting widget %s: %s", widget_id, e)


def _normalize_pages_list(pages_in: list) -> list[dict]:
    """Match MachineStateService.set_pages widget-list normalization."""
    normalized: list[dict] = []
    for page in pages_in or []:
        if not isinstance(page, dict):
            continue
        page_copy = dict(page)
        widgets = page_copy.get("widgets")
        if not isinstance(widgets, list):
            page_copy["widgets"] = []
        normalized.append(page_copy)
    return normalized


def _widget_config_fingerprint(widget: dict) -> str:
    return json.dumps(
        {
            "id": widget.get("id"),
            "position": widget.get("position"),
            "fields": widget.get("fields"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def try_soft_apply_remote_supabase_pages(ctx: AppContext, new_pages: list) -> bool:
    """
    After conf.json was written from a Supabase pages snapshot, patch ctx.pages so
    the main loop can skip reload_pages when safe.

    Returns True to skip full soft reload when:
    - UI is in widget mode (a page may be visible).
    - Content page UUIDs match the snapshot in the same order (synthetic ``0`` tail ignored).
    - Per-widget ``id`` / ``position`` / ``fields`` changes kill stale processes; the
      active page's changed widgets are restarted immediately.
    """
    if ctx.state_str != "widget" or not ctx.pages:
        return False

    normalized = _normalize_pages_list(new_pages)
    by_uuid = {p["uuid"]: p for p in normalized if isinstance(p, dict) and p.get("uuid")}

    content_uuids_rt = [p.get("uuid") for p in ctx.pages if p.get("uuid") != "0"]
    content_uuids_new = [p.get("uuid") for p in normalized if p.get("uuid")]
    if content_uuids_rt != content_uuids_new:
        return False

    pi = ctx.page_index
    if pi < 0 or pi >= len(ctx.pages):
        return False

    try:
        for page in ctx.pages:
            pu = page.get("uuid")
            if not pu or pu == "0":
                continue
            new_cfg = by_uuid.get(pu)
            if new_cfg is None:
                return False
            old_entries = page.get("widgets") or []
            new_cfgs = new_cfg.get("widgets") or []
            if len(old_entries) != len(new_cfgs):
                return False
            for we in old_entries:
                if not isinstance(we.get("widget"), dict):
                    return False

        for page_idx, page in enumerate(ctx.pages):
            pu = page.get("uuid")
            if not pu or pu == "0":
                continue
            new_cfg = by_uuid[pu]
            for key in ("duration", "title", "combination"):
                if key in new_cfg:
                    page[key] = new_cfg[key]
            if "enabled" in new_cfg:
                page["enabled"] = bool(new_cfg.get("enabled", True))

            old_entries = page.get("widgets") or []
            new_cfgs = new_cfg.get("widgets") or []
            for j, new_w in enumerate(new_cfgs):
                if not isinstance(new_w, dict):
                    return False
                widget_entry = old_entries[j]
                inner = widget_entry["widget"]
                if _widget_config_fingerprint(inner) == _widget_config_fingerprint(new_w):
                    continue
                old_id = str(inner.get("id") or "")
                _kill_widget_process(
                    widget_entry, old_id, "supabase pages widget config change"
                )
                widget_entry["widget"] = copy.deepcopy(new_w)
                if page_idx == pi:
                    restart_widget_process(widget_entry, page, j)
    except Exception as e:
        _log.warning(
            "supabase pages soft apply failed; falling back to full reload: %s", e
        )
        return False

    _log.info("supabase pages: soft-applied in widget mode (skipped reload_pages)")
    return True


def check_page_widget_updates(page: dict, get_context) -> None:
    """Check widgets on page for updates (throttled); start background download if needed."""
    current_time = time.time()
    check_interval = 3600
    for widget_entry in page["widgets"]:
        widget = widget_entry.get("widget")
        if widget is None:
            continue
        widget_id = widget.get("id")
        if widget_id is None or widget_id == "0":
            continue
        last_check = widget_update_checks.get(widget_id, 0)
        if current_time - last_check <= check_interval:
            continue
        try:
            needs_update, download_info = check_and_update_widget_version(widget_id)
            widget_update_checks[widget_id] = current_time
            if needs_update and download_info is not None:
                url = download_info.get("widget_download_url")
                md5 = download_info.get("widget_download_md5")
                if url and md5:
                    download_widget_async(widget_id, url, md5, get_context)
        except Exception as e:
            _log.warning("Error checking widget version for %s: %s", widget_id, e)


def start_page_process(page: dict) -> dict:
    """Start all widget processes for a page; return page dict with widgets and framebuffer or None."""
    widgets = []
    for widget_index in range(len(page["widgets"])):
        widget = page["widgets"][widget_index]
        if widget["id"] == "0":
            fallback_entry = {
                "process": None,
                "shm": None,
                "widget": widget,
                "launched": False,
                "has_small_widget": None,
            }
            page_uuid = page["uuid"]
            shm_name = f"widget_{page_uuid}_{widget_index}_shm"
            shm_size = (widget["position"][2] - widget["position"][0] + 1) * (
                widget["position"][3] - widget["position"][1] + 1
            ) * 3 + 1
            try:
                existing = shared_memory.SharedMemory(name=shm_name)
                existing.close()
                shared_memory.SharedMemory(name=shm_name).unlink()
            except (FileNotFoundError, FileExistsError):
                pass
            try:
                shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
                command = [
                    os.path.join(os.getcwd(), "venv0/bin/python"),
                    os.path.join(os.getcwd(), "default.py"),
                ]
                command.extend(["--params", "{}", "--shm", shm_name])
                command.extend(["--data-store", get_user_data_store_path("0")])
                process = subprocess.Popen(
                    command, cwd=os.getcwd(), preexec_fn=set_pdeathsig
                )
                widgets.append(
                    {
                        "process": process,
                        "shm": shm,
                        "widget": widget,
                        "launched": False,
                        "has_small_widget": None,
                    }
                )
            except Exception as e:
                _log.error("Error starting default widget: %s", e)
                widgets.append(fallback_entry)
            continue
        widget_path = os.path.join(os.getcwd(), "apps", widget["id"])
        if not os.path.isdir(widget_path):
            _request_missing_widget_download(widget["id"], force=True)
            widgets.append(
                {
                    "process": None,
                    "shm": None,
                    "widget": widget,
                    "launched": False,
                    "has_small_widget": None,
                }
            )
            continue
        if os.path.isdir(widget_path):
            page_uuid = page["uuid"]
            shm_name = f"widget_{page_uuid}_{widget_index}_shm"
            shm_size = (widget["position"][2] - widget["position"][0] + 1) * (
                widget["position"][3] - widget["position"][1] + 1
            ) * 3 + 1
            try:
                existing = shared_memory.SharedMemory(name=shm_name)
                existing.close()
                shared_memory.SharedMemory(name=shm_name).unlink()
            except (FileNotFoundError, FileExistsError):
                pass
            try:
                shm = shared_memory.SharedMemory(name=shm_name, create=True, size=shm_size)
                shm.buf[0] = 1
                command = [
                    os.path.join(os.getcwd(), "venv0/bin/python"),
                    os.path.join(os.getcwd(), "apps/", widget["id"], "main.py"),
                ]
                command.extend(
                    [
                        "--params",
                        json.dumps(process_widget_fields(widget["id"], widget["fields"])),
                        "--shm",
                        shm_name,
                        "--data-store",
                        get_user_data_store_path(widget["id"]),
                    ]
                )
                process = subprocess.Popen(
                    command,
                    cwd=os.path.join("./apps/", widget["id"]),
                    preexec_fn=set_pdeathsig,
                )
                widgets.append(
                    {
                        "process": process,
                        "shm": shm,
                        "widget": widget,
                        "launched": False,
                        "has_small_widget": None,
                    }
                )
            except Exception as e:
                _log.error("Error starting widget %s: %s", widget["id"], e)
                widgets.append(
                    {
                        "process": None,
                        "shm": None,
                        "widget": widget,
                        "launched": False,
                        "has_small_widget": None,
                    }
                )
    img = Image.new("RGB", (128, 160), (0, 0, 0))
    for w in widgets:
        try:
            proc = w.get("process")
            if proc is None:
                continue
            os.kill(proc.pid, signal.SIGSTOP)
            w["launched"] = False
        except Exception as e:
            _log.warning("Error pausing widget process: %s", e)
    return {
        "widgets": widgets,
        "duration": page["duration"],
        "uuid": page["uuid"],
        "framebuffer": bytearray(img.tobytes()),
        "enabled": page.get("enabled", True),
    }


def init_pages(config: dict) -> list:
    """Initialize pages from config and add default widget page."""
    pages = []
    for page in config.get("pages", []):
        pp = start_page_process(page)
        if pp is not None:
            pages.append(pp)
    default = start_page_process(
        {
            "uuid": "0",
            "title": "default widget",
            "duration": "60",
            "combination": "0",
            "enabled": True,
            "widgets": [
                {"id": "0", "position": [0, 0, 127, 159], "fields": {}}
            ],
        }
    )
    if default:
        pages.append(default)
    return pages


def term_widget_processes(pages: list) -> None:
    """Terminate all widget processes and clear pages."""
    if pages is None:
        return
    for page in pages:
        for widget in page.get("widgets", []):
            try:
                p = widget.get("process")
                if p and p.poll() is None:
                    os.kill(p.pid, signal.SIGCONT)
                    os.kill(p.pid, signal.SIGKILL)
                shm = widget.get("shm")
                if shm:
                    shm.close()
                    shm.unlink()
                widget.clear()
            except Exception as e:
                _log.warning("Error terminating widget: %s", e)
        page.clear()
    pages.clear()
