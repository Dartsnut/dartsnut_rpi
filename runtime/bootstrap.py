"""
Background threads and remote sync startup (BLE, WebSocket, Supabase, network pollers).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Callable, Dict, Optional

import assets
from runtime import device_json_identity
from runtime.remote_sync_port import get_remote_sync

_log = logging.getLogger(__name__)
_UDP_BROADCAST_THREAD_ENABLED = False


def _normalize_device_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = raw.split(":")
    if len(parts) == 6 and all(len(p) == 2 and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts):
        return ":".join(p.upper() for p in parts)
    return raw


def _has_identity_fields(device_info: Dict[str, Any]) -> bool:
    if not isinstance(device_info, dict):
        return False
    serial = str(device_info.get("serial", "")).strip()
    model = str(device_info.get("model", "")).strip()
    return bool(serial and model)


def _ensure_device_info_id(device_info: Dict[str, Any]) -> Dict[str, Any]:
    info = dict(device_info or {})
    existing_id = _normalize_device_id(info.get("id"))
    resolved = existing_id or _normalize_device_id(
        info.get("ble_mac") or info.get("mac_address")
    )
    if not resolved:
        try:
            from bluezero import adapter  # type: ignore

            adapters = list(adapter.Adapter.available())
            if adapters:
                resolved = _normalize_device_id(adapters[0].address)
        except Exception:
            resolved = ""
    if not resolved:
        return info

    info["id"] = resolved

    try:
        path = os.path.join(os.getcwd(), "device.json")
        persisted: Dict[str, Any] = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                persisted = json.load(f) or {}

        if not _has_identity_fields(persisted):
            boot_identity = device_json_identity.load_boot_device_identity()
            if boot_identity:
                persisted = dict(persisted)
                for key in ("serial", "model"):
                    val = str(boot_identity.get(key, "")).strip()
                    if val:
                        persisted[key] = val
                for key in ("serial", "model"):
                    if not str(info.get(key, "")).strip() and str(
                        persisted.get(key, "")
                    ).strip():
                        info[key] = persisted[key]

        persisted = dict(persisted or {})
        info = dict(info)
        persisted["id"] = resolved
        for key in ("serial", "model"):
            if not str(persisted.get(key, "")).strip():
                incoming = str(info.get(key, "")).strip()
                if incoming:
                    persisted[key] = incoming

        persisted = device_json_identity.apply_factory_serial_if_needed(
            persisted, device_id=resolved
        )
        info = device_json_identity.apply_factory_serial_if_needed(
            info, device_id=resolved
        )
        for key in ("serial", "model"):
            if not str(info.get(key, "")).strip() and str(persisted.get(key, "")).strip():
                info[key] = persisted[key]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(persisted, f)
        device_json_identity.verify_and_repair_device_json(path)
    except Exception:
        pass

    return info


def start_background_subsystems(
    *,
    dartsnut: Any,
    device_info: Dict[str, Any],
    get_version: Callable[[], Any],
    set_volume: Callable[[int], None],
    start_ble_server: Callable[..., None],
    locate_device: Callable[[], None],
    start_websocket_server: Callable[..., None],
    set_brightness: Callable[[int], None],
    reload_config: Callable[[], None],
    set_time_zone: Callable[[str], Any],
    get_widgets_framebuffer: Callable[[], Any],
    start_game_from_websocket: Callable[[str], bool],
    trigger_dim_check: Callable[[], None],
    check_connection_loop: Callable[[], None],
    network_state_remote_loop: Callable[[], None],
    controller_status_loop: Callable[[], None],
    apply_remote_config: Callable[[Dict[str, Any]], None],
    on_sync_game_ready: Optional[Callable[[Any], None]] = None,
    on_remote_connectivity_changed: Callable[[bool], None],
    request_network_state_refresh: Callable[[], None],
    remote_config_runtime: Any,
    websocket_service_registry: Any = None,
    set_startup_volume: Optional[Callable[[int], None]] = None,
    sideload_manager: Any = None,
) -> None:
    # Keep UDP discovery broadcasts disabled in this branch.
    # IP/SSID reads still come from machine_api -> udp_broadcast helpers.
    if not _UDP_BROADCAST_THREAD_ENABLED:
        _log.info("UDP discovery broadcast thread disabled")

    device_info = _ensure_device_info_id(device_info)
    dartsnut.update_frame_buffer(assets.create_loading_image())
    try:
        version_result = get_version()
        firmware_version = "dev"
        if (
            isinstance(version_result, dict)
            and not version_result.get("error")
            and version_result.get("version")
        ):
            firmware_version = str(version_result.get("version"))
        device_info["firmware_version"] = firmware_version
        remote_config_runtime.startup_firmware_version = firmware_version
    except Exception as e:
        _log.warning("Error determining firmware version for remote initial state: %s", e)
    if "firmware_update" not in device_info:
        device_info["firmware_update"] = False
    try:
        (set_startup_volume or set_volume)(int(device_info.get("volume", "50")))
    except Exception as e:
        _log.warning("Error applying startup volume: %s", e)

    threading.Thread(
        target=start_ble_server, args=(locate_device,), daemon=True
    ).start()
    threading.Thread(
        target=start_websocket_server,
        args=(
            set_brightness,
            locate_device,
            reload_config,
            set_time_zone,
            get_widgets_framebuffer,
            start_game_from_websocket,
            set_volume,
            trigger_dim_check,
            websocket_service_registry,
            None,
            sideload_manager,
        ),
        daemon=True,
    ).start()
    threading.Thread(target=check_connection_loop, daemon=True).start()
    threading.Thread(target=network_state_remote_loop, daemon=True).start()
    threading.Thread(target=controller_status_loop, daemon=True).start()

    get_remote_sync().set_connectivity_callback(on_remote_connectivity_changed)
    request_network_state_refresh()

    try:
        get_remote_sync().start_sync_if_available(
            device_info or {},
            reload_config,
            apply_remote_config,
            on_sync_game_ready,
        )
    except Exception as e:
        _log.error("Failed to start Supabase sync: %s", e)
    else:
        remote_config_runtime.awaiting_games_ready_confirmation = True
        remote_config_runtime.startup_settlement_completed = False
        remote_config_runtime.startup_games_ready_confirmed_at = None
        remote_config_runtime.startup_filter_playing_until_newer_update = False
