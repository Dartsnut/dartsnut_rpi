"""
MachineStateService: single entrypoint for core machine state changes.

This service is responsible for:
- Applying changes to hardware / AppContext (brightness, pages, etc.)
- Persisting relevant fields to local JSON files (device.json, apps/conf.json)

It deliberately does NOT talk to Firestore directly. Higher layers (e.g.
firestore_sync_bridge, websocket handlers) decide when to call this service
based on whether Firestore is the source of truth.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app_context import AppContext


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
            print(f"Error writing device.json: {e}")

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
            print(f"Error updating firmware info in device.json: {e}")

    def set_brightness(self, brightness: int) -> None:
        """
        Set brightness on hardware and persist to device.json.
        Smooth transition timing/state is still handled in main.py; this method
        just sets the immediate hardware value and JSON field.
        """
        try:
            self._set_brightness_hardware(brightness)
        except Exception as e:
            print(f"Failed to set brightness hardware: {e}")

        try:
            device_info = self._get_device_info() or {}
            device_info["brightness"] = str(brightness)
            self._write_device_info(device_info)
        except Exception as e:
            print(f"Error updating brightness in device.json: {e}")

    def set_volume(self, volume: int) -> None:
        """
        Set volume via amixer and persist to device.json.
        Mirrors the behavior of main.set_volume without any Firestore concerns.
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
            print(f"Failed to set volume: {stderr}")
        except Exception as e:
            print(f"Error setting volume: {e}")

        try:
            device_info = self._get_device_info() or {}
            device_info["volume"] = str(volume)
            self._write_device_info(device_info)
        except Exception as e:
            print(f"Error updating volume in device.json: {e}")

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
            print(f"Error updating dim window in device.json: {e}")

    def set_pages(self, pages: List[Dict[str, Any]]) -> None:
        """
        Persist pages to ./apps/conf.json and reload pages into AppContext.
        """
        try:
            apps_dir = os.path.join(os.getcwd(), "apps")
            os.makedirs(apps_dir, exist_ok=True)
            conf_path = os.path.join(apps_dir, "conf.json")
            payload: Dict[str, Any] = {
                "pages": pages,
                "pages_updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(conf_path, "w") as f:
                json.dump(payload, f)
        except Exception as e:
            print(f"Error writing apps/conf.json: {e}")
            return

        try:
            self._reload_pages_from_conf(self._ctx)
        except Exception as e:
            print(f"Error reloading pages from conf.json: {e}")

    def set_device_name(self, name: str) -> None:
        """
        Update the device 'name' field in device.json.
        """
        try:
            device_info = self._read_device_info()
            device_info["name"] = name
            self._write_device_info(device_info)
        except Exception as e:
            print(f"Error setting device name in device.json: {e}")


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

