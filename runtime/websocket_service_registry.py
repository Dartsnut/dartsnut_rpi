from __future__ import annotations

from typing import Any, Callable

from python_websocket import bluetooth_operations as btops
from python_websocket import device_operations as devops
from python_websocket.error_handler import handle_exception
from python_websocket import file_operations as fops
from python_websocket import git_operations as gitops
from python_websocket import json_operations as jops
from python_websocket import user_data_operations as udops
from runtime.websocket_ports import (
    BluetoothOpsPort,
    DeviceOpsPort,
    FileOpsPort,
    GitOpsPort,
    JsonOpsPort,
    UserDataOpsPort,
    WebsocketServiceRegistry,
)


def _adapt(action: str, fn: Callable[..., dict[str, Any]], context: str) -> Callable[..., dict]:
    def wrapped(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return handle_exception(action, exc, context)

    return wrapped


def build_default_websocket_registry(
    *, perform_update: Callable[..., dict[str, Any]] | None = None
) -> WebsocketServiceRegistry:
    update_fn = perform_update or gitops.perform_update
    return WebsocketServiceRegistry(
        file_ops=FileOpsPort(
            receive_file=_adapt("send_file", fops.receive_file, "Failed to receive file"),
            send_file=_adapt("get_file", fops.send_file, "Failed to send file"),
            remove_directory=_adapt(
                "remove_directory", fops.remove_directory, "Failed to remove directory"
            ),
            get_file_md5=_adapt("get_file_md5", fops.get_file_md5, "Failed to get file checksum"),
            get_file_list=_adapt("list_files", fops.get_file_list, "Failed to list files"),
            create_directory=_adapt(
                "create_directory", fops.create_directory, "Failed to create directory"
            ),
            download_app=_adapt("download_app", fops.download_app, "Download failed"),
            get_app_list=_adapt("list_apps", fops.get_app_list, "Failed to list apps"),
            start_game_download_async=_adapt(
                "download_app",
                fops.start_game_download_async,
                "Failed to start game download",
            ),
            start_game_download_async_with_url=_adapt(
                "download_app",
                fops.start_game_download_async_with_url,
                "Failed to start game download",
            ),
            get_download_progress=_adapt(
                "get_download_progress",
                fops.get_download_progress,
                "Failed to get download progress",
            ),
        ),
        json_ops=JsonOpsPort(
            read_json_file=_adapt("read_json", jops.read_json_file, "Failed to read JSON file"),
            write_json_file=_adapt(
                "write_json", jops.write_json_file, "Failed to write JSON file"
            ),
            get_device_info=_adapt(
                "get_device_info", jops.get_device_info, "Failed to get device info"
            ),
            set_device_name=_adapt(
                "set_device_name", jops.set_device_name, "Failed to set device name"
            ),
        ),
        bluetooth_ops=BluetoothOpsPort(
            scan_bluetooth_devices=_adapt(
                "bluetooth_scan", btops.scan_bluetooth_devices, "Failed to scan bluetooth devices"
            ),
            list_paired_devices=_adapt(
                "bluetooth_list", btops.list_paired_devices, "Failed to list paired devices"
            ),
            disconnect_and_unpair_device=_adapt(
                "bluetooth_remove",
                btops.disconnect_and_unpair_device,
                "Failed to remove bluetooth device",
            ),
            pair_and_connect_device=_adapt(
                "bluetooth_connect",
                btops.pair_and_connect_device,
                "Failed to connect bluetooth device",
            ),
        ),
        git_ops=GitOpsPort(
            check_update=_adapt("check_update", gitops.check_update, "Failed to check update"),
            perform_update=_adapt("perform_update", update_fn, "Failed to update"),
            get_version=_adapt("get_version", gitops.get_version, "Failed to get version"),
        ),
        device_ops=DeviceOpsPort(
            get_wifi_rssi=_adapt("get_wifi_rssi", devops.get_wifi_rssi, "Failed to get wifi RSSI"),
            forget_wifi=_adapt("forget_wifi", devops.forget_wifi, "Failed to forget wifi"),
            reboot=devops.reboot,
            get_ssh_status=_adapt(
                "get_ssh_status", devops.get_ssh_status, "Failed to get ssh status"
            ),
            start_ssh=_adapt("start_ssh", devops.start_ssh, "Failed to start ssh"),
            stop_ssh=_adapt("stop_ssh", devops.stop_ssh, "Failed to stop ssh"),
            get_brightness=_adapt(
                "get_brightness", devops.get_brightness, "Failed to get brightness"
            ),
            get_volume=_adapt("get_volume", devops.get_volume, "Failed to get volume"),
            get_dim_window=_adapt(
                "get_dim_window", devops.get_dim_window, "Failed to get dim settings"
            ),
            set_dim_window=_adapt(
                "set_dim_window", devops.set_dim_window, "Failed to set dim settings"
            ),
        ),
        user_data_ops=UserDataOpsPort(
            get_user_data=_adapt(
                "get_user_data", udops.get_user_data, "Failed to get user data"
            ),
            update_user_info=_adapt(
                "update_user_info", udops.update_user_info, "Failed to update user info"
            ),
            get_game_playtime=_adapt(
                "get_game_playtime", udops.get_game_playtime, "Failed to get game playtime"
            ),
        ),
    )


def build_default_machine_registry() -> WebsocketServiceRegistry:
    return build_default_websocket_registry()
