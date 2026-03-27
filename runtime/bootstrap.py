"""
Background threads and remote sync startup (BLE, WebSocket, Supabase, network pollers).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict

import assets
from runtime.remote_sync_port import get_remote_sync


def start_background_subsystems(
    *,
    dartsnut: Any,
    device_info: Dict[str, Any],
    get_version: Callable[[], Any],
    set_volume: Callable[[int], None],
    start_ble_server: Callable[..., None],
    start_udp_broadcast: Callable[[], None],
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
    apply_remote_config: Callable[[Dict[str, Any]], None],
    on_remote_connectivity_changed: Callable[[bool], None],
    request_network_state_refresh: Callable[[], None],
    remote_config_runtime: Any,
    websocket_service_registry: Any = None,
) -> None:
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
        print(f"Error determining firmware version for remote initial state: {e}")
    if "firmware_update" not in device_info:
        device_info["firmware_update"] = False
    set_volume(int(device_info.get("volume", "50")))

    threading.Thread(
        target=start_ble_server, args=(locate_device,), daemon=True
    ).start()
    threading.Thread(target=start_udp_broadcast, daemon=True).start()

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
        ),
        daemon=True,
    ).start()
    threading.Thread(target=check_connection_loop, daemon=True).start()
    threading.Thread(target=network_state_remote_loop, daemon=True).start()

    get_remote_sync().set_connectivity_callback(on_remote_connectivity_changed)
    request_network_state_refresh()

    try:
        get_remote_sync().start_sync_if_available(
            device_info or {}, reload_config, apply_remote_config
        )
    except Exception as e:
        print(f"Failed to start Supabase sync: {e}")
    else:
        try:
            get_remote_sync().request_set_all_games_ready()
            remote_config_runtime.awaiting_games_ready_confirmation = True
            remote_config_runtime.startup_games_ready_confirmed_at = None
            remote_config_runtime.startup_filter_playing_until_newer_update = False
        except Exception as e:
            print(f"Error resetting remote game statuses to ready on startup: {e}")
