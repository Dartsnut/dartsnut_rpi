from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class FileOpsPort:
    receive_file: Callable[..., dict]
    send_file: Callable[..., dict]
    remove_directory: Callable[..., dict]
    get_file_md5: Callable[..., dict]
    get_file_list: Callable[..., dict]
    create_directory: Callable[..., dict]
    download_app: Callable[..., dict]
    get_app_list: Callable[..., dict]
    start_game_download_async: Callable[..., dict]
    start_game_download_async_with_url: Callable[..., dict]
    get_download_progress: Callable[..., dict]


@dataclass(frozen=True)
class JsonOpsPort:
    read_json_file: Callable[..., dict]
    write_json_file: Callable[..., dict]
    get_device_info: Callable[..., dict]
    set_device_name: Callable[..., dict]


@dataclass(frozen=True)
class BluetoothOpsPort:
    scan_bluetooth_devices: Callable[..., dict]
    list_paired_devices: Callable[..., dict]
    disconnect_and_unpair_device: Callable[..., dict]
    pair_and_connect_device: Callable[..., dict]


@dataclass(frozen=True)
class GitOpsPort:
    check_update: Callable[..., dict]
    perform_update: Callable[..., dict]
    get_version: Callable[..., dict]


@dataclass(frozen=True)
class DeviceOpsPort:
    get_wifi_rssi: Callable[..., dict]
    forget_wifi: Callable[..., Any]
    reboot: Callable[..., Any]
    get_ssh_status: Callable[..., dict]
    start_ssh: Callable[..., dict]
    stop_ssh: Callable[..., dict]
    get_brightness: Callable[..., dict]
    get_volume: Callable[..., dict]
    get_dim_window: Callable[..., dict]
    set_dim_window: Callable[..., dict]


@dataclass(frozen=True)
class UserDataOpsPort:
    get_user_data: Callable[..., dict]
    update_user_info: Callable[..., dict]
    get_game_playtime: Callable[..., dict]


@dataclass(frozen=True)
class WebsocketServiceRegistry:
    file_ops: FileOpsPort
    json_ops: JsonOpsPort
    bluetooth_ops: BluetoothOpsPort
    git_ops: GitOpsPort
    device_ops: DeviceOpsPort
    user_data_ops: UserDataOpsPort


# Machine-facing naming for the same capability contract.
MachineFileOpsPort = FileOpsPort
MachineJsonOpsPort = JsonOpsPort
MachineBluetoothOpsPort = BluetoothOpsPort
MachineGitOpsPort = GitOpsPort
MachineDeviceOpsPort = DeviceOpsPort
MachineUserDataOpsPort = UserDataOpsPort
MachineServiceRegistry = WebsocketServiceRegistry


@dataclass(frozen=True)
class WebsocketEndpointConfig:
    set_brightness: Optional[Callable[[int], None]] = None
    locate_device: Optional[Callable[[], None]] = None
    reload_config: Optional[Callable[[], None]] = None
    set_time_zone: Optional[Callable[[str], Any]] = None
    get_widgets_framebuffer: Optional[Callable[[], Any]] = None
    start_game_process: Optional[Callable[[str], bool]] = None
    set_volume: Optional[Callable[[int], None]] = None
    trigger_dim_check: Optional[Callable[[], None]] = None
    sideload_manager: Any = None
