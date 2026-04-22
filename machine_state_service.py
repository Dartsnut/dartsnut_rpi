"""
MachineStateService: single entrypoint for core machine state changes.

This service is responsible for:
- Applying changes to hardware / AppContext (brightness, pages, etc.)
- Persisting relevant fields to local JSON files (device.json, apps/conf.json)

It deliberately does NOT talk to remote sync providers directly. Higher layers
(e.g. sync bridge, websocket handlers) decide when to call this service based
on source-of-truth rules.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from domain.app_context import AppContext

_log = logging.getLogger(__name__)


class MachineStateService:
    def __init__(
        self,
        ctx: AppContext,
        set_brightness_hardware: Callable[[int], None],
        get_device_info: Callable[[], Dict[str, Any]],
        reload_pages_from_conf: Callable[[AppContext], None],
    ) -> None:
        self._ctx = ctx
        self._set_brightness_hardware = set_brightness_hardware
        self._get_device_info = get_device_info
        self._reload_pages_from_conf = reload_pages_from_conf

    # ------------------------------------------------------------------
    # device.json helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _device_json_path() -> str:
        return os.path.join(os.getcwd(), "device.json")

    @classmethod
    def _read_device_info(cls) -> Dict[str, Any]:
        path = cls._device_json_path()
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def _write_device_info(cls, device_info: Dict[str, Any]) -> None:
        path = cls._device_json_path()
        # Always update the device-level timestamp whenever we persist.
        try:
            device_info = dict(device_info)
            device_info["updated_at"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            # If timestamping fails for any reason, fall back to raw write.
            pass
        try:
            with open(path, "w") as f:
                json.dump(device_info, f)
        except Exception as e:
            _log.error("Error writing device.json: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_firmware_info(self, version: str, update: bool) -> None:
        """
        Persist firmware metadata (version + update flag) to device.json.
        """
        try:
            device_info = self._read_device_info()
            device_info["firmware_version"] = version
            device_info["firmware_update"] = bool(update)
            self._write_device_info(device_info)
        except Exception as e:
            _log.error("Error updating firmware info in device.json: %s", e)

    def set_brightness(self, brightness: int) -> None:
        """
        Set brightness on hardware and persist to device.json.
        Smooth transition timing/state is still handled in main.py; this method
        just sets the immediate hardware value and JSON field.
        """
        try:
            self._set_brightness_hardware(brightness)
        except Exception as e:
            _log.warning("Failed to set brightness hardware: %s", e)

        try:
            device_info = self._get_device_info() or {}
            device_info["brightness"] = str(brightness)
            self._write_device_info(device_info)
        except Exception as e:
            _log.error("Error updating brightness in device.json: %s", e)

    def set_volume(self, volume: int) -> None:
        """
        Set volume via amixer and persist to device.json.
        Mirrors the behavior of main.set_volume without sync-provider concerns.
        """
        try:
            if volume == 0:
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "PCM", "mute"],
                    check=True,
                    capture_output=True,
                )
            else:
                mapped_volume = int(50 + (volume / 100) * 50)
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "PCM", "unmute"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["amixer", "-c", "0", "sset", "PCM", f"{mapped_volume}%"],
                    check=True,
                    capture_output=True,
                )
        except subprocess.CalledProcessError as e:
            try:
                stderr = e.stderr.decode().strip() if e.stderr else str(e)
            except Exception:
                stderr = str(e)
            _log.warning("Failed to set volume: %s", stderr)
        except Exception as e:
            _log.warning("Error setting volume: %s", e)

        try:
            device_info = self._get_device_info() or {}
            device_info["volume"] = str(volume)
            self._write_device_info(device_info)
        except Exception as e:
            _log.error("Error updating volume in device.json: %s", e)

    def set_dim_window(self, config: Dict[str, Any]) -> None:
        """
        Update dim-window related fields in device.json.

        Expected keys in config:
        - dim_window_enabled (bool)
        - dim_window_start (str 'HH:MM')
        - dim_window_end (str 'HH:MM')
        - dim_level (int)
        - dim_restore_seconds (int)
        """
        try:
            device_info = self._get_device_info() or {}
            if "dim_window_enabled" in config:
                device_info["dim_window_enabled"] = bool(
                    config.get("dim_window_enabled", False)
                )
            if "dim_window_start" in config:
                device_info["dim_window_start"] = config.get(
                    "dim_window_start", device_info.get("dim_window_start", "")
                )
            if "dim_window_end" in config:
                device_info["dim_window_end"] = config.get(
                    "dim_window_end", device_info.get("dim_window_end", "")
                )
            if "dim_level" in config:
                raw_level = config.get("dim_level")
                if raw_level is not None:
                    device_info["dim_level"] = int(
                        raw_level if raw_level != "" else device_info.get("dim_level", 0)
                    )
            if "dim_restore_seconds" in config:
                raw_secs = config.get("dim_restore_seconds")
                if raw_secs is not None:
                    device_info["dim_restore_seconds"] = int(
                        raw_secs
                        if raw_secs != ""
                        else device_info.get("dim_restore_seconds", 0)
                    )
            self._write_device_info(device_info)
        except Exception as e:
            _log.error("Error updating dim window in device.json: %s", e)

    def set_pages(
        self,
        pages: List[Dict[str, Any]],
        *,
        reload_pages: bool = True,
    ) -> None:
        """
        Persist pages to ./apps/conf.json.

        When reload_pages is True (default), immediately reload pages into
        AppContext. Callers that need websocket-style deferred reload behavior
        can pass reload_pages=False and trigger ctx.reload_pages in the main
        loop instead.
        """
        try:
            # Normalize widgets lists so we never persist \"widgets\": null.
            normalized_pages: List[Dict[str, Any]] = []
            for page in pages or []:
                if not isinstance(page, dict):
                    continue
                page_copy = dict(page)
                widgets = page_copy.get("widgets")
                if not isinstance(widgets, list):
                    page_copy["widgets"] = []
                normalized_pages.append(page_copy)

            apps_dir = os.path.join(os.getcwd(), "apps")
            os.makedirs(apps_dir, exist_ok=True)
            conf_path = os.path.join(apps_dir, "conf.json")

            # Preserve any existing top-level keys in conf.json (e.g. user/date)
            # and only replace the pages-related fields.
            existing: Dict[str, Any] = {}
            try:
                if os.path.isfile(conf_path):
                    with open(conf_path, "r") as f:
                        existing = json.load(f) or {}
            except Exception:
                existing = {}

            payload: Dict[str, Any] = dict(existing)
            payload["pages"] = normalized_pages
            payload["pages_updated_at"] = datetime.now(timezone.utc).isoformat()

            with open(conf_path, "w") as f:
                json.dump(payload, f)
        except Exception as e:
            _log.error("Error writing apps/conf.json: %s", e)
            return

        _log.info(
            "machine state: wrote %s pages to apps/conf.json (reload_pages=%s)",
            len(normalized_pages),
            reload_pages,
        )

        if not reload_pages:
            return

        try:
            self._reload_pages_from_conf(self._ctx)
        except Exception as e:
            _log.error("Error reloading pages from conf.json: %s", e)

    def set_device_name(self, name: str) -> None:
        """
        Update the device 'name' field in device.json.
        """
        try:
            device_info = self._read_device_info()
            device_info["name"] = name
            self._write_device_info(device_info)
        except Exception as e:
            _log.error("Error setting device name in device.json: %s", e)

    def clear_apps_directory_contents(self) -> None:
        """
        Remove all files and directories inside ./apps while keeping ./apps itself.
        Preserve apps/conf.json and rewrite it to an empty config with pages=[].
        """
        _log.info("machine state: clearing apps directory contents (device reset path)")
        apps_dir = os.path.join(os.getcwd(), "apps")
        try:
            os.makedirs(apps_dir, exist_ok=True)
            for entry in os.listdir(apps_dir):
                target = os.path.join(apps_dir, entry)
                if entry == "conf.json":
                    continue
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    try:
                        os.remove(target)
                    except FileNotFoundError:
                        pass
            conf_path = os.path.join(apps_dir, "conf.json")
            with open(conf_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "user": "",
                        "date": "",
                        "pages": [],
                        "pages_updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    f,
                )
        except Exception as e:
            _log.error("Error clearing apps directory contents: %s", e)

    def reset_device_to_factory_fields(self) -> None:
        """
        Reset mutable factory fields while preserving device identity metadata.
        """
        _log.info("machine state: resetting device.json to factory fields")
        try:
            existing = self._read_device_info() or {}
            payload = dict(existing)
            payload["brightness"] = 100
            payload["volume"] = 100
            payload["ssid"] = ""
            payload["ip_address"] = ""
            payload["dim_window_enabled"] = False
            payload["dim_window_start"] = "22:00"
            payload["dim_window_end"] = "8:00"
            payload["dim_level"] = 10
            payload["dim_restore_seconds"] = 5
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            path = self._device_json_path()
            with open(path, "w") as f:
                json.dump(payload, f)
        except Exception as e:
            _log.error("Error resetting device.json to factory fields: %s", e)


_service: Optional[MachineStateService] = None


def init_machine_state_service(
    ctx: AppContext,
    set_brightness_hardware: Callable[[int], None],
    get_device_info: Callable[[], Dict[str, Any]],
    reload_pages_from_conf: Callable[[AppContext], None],
) -> None:
    global _service
    _service = MachineStateService(
        ctx,
        set_brightness_hardware=set_brightness_hardware,
        get_device_info=get_device_info,
        reload_pages_from_conf=reload_pages_from_conf,
    )


def get_machine_state_service() -> Optional[MachineStateService]:
    return _service

