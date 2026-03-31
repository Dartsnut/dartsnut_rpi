"""
Local machine I/O facade for the presentation layer and composition root.

Keeps `states/*` from importing python_websocket or remote-sync modules directly.
Game downloads and app files remain in `game_lifecycle` / `widget_lifecycle` for now;
call those modules from here when adding new presentation-layer entry points.
"""

from __future__ import annotations

import importlib


def stop_game_tracking() -> None:
    udo = importlib.import_module("python_websocket.user_data_operations")

    udo.stop_game_tracking()


def reset_user_data_file() -> None:
    udo = importlib.import_module("python_websocket.user_data_operations")

    udo.reset_user_data_file()


def parse_hhmm(value: str):
    devops = importlib.import_module("python_websocket.device_operations")

    return devops._parse_hhmm(value)


def forget_wifi() -> None:
    devops = importlib.import_module("python_websocket.device_operations")

    devops.forget_wifi()


def get_version():
    gitops = importlib.import_module("python_websocket.git_operations")

    return gitops.get_version()


def perform_update():
    gitops = importlib.import_module("python_websocket.git_operations")

    return gitops.perform_update()


def build_remote_bluetooth_list():
    btops = importlib.import_module("python_websocket.bluetooth_operations")

    return btops.build_remote_bluetooth_list()


def connect_device_for_remote(address: str):
    btops = importlib.import_module("python_websocket.bluetooth_operations")

    return btops.connect_device_for_remote(address)


def current_utc_iso_timestamp() -> str:
    btops = importlib.import_module("python_websocket.bluetooth_operations")

    return btops.current_utc_iso_timestamp()


def get_ip_address():
    udp = importlib.import_module("python_websocket.udp_broadcast")

    return udp.get_ip_address()


def get_current_ssid():
    udp = importlib.import_module("python_websocket.udp_broadcast")

    return udp.get_current_ssid()


def normalize_ip(value):
    udp = importlib.import_module("python_websocket.udp_broadcast")

    return udp.normalize_ip(value)


def normalize_ssid(value):
    udp = importlib.import_module("python_websocket.udp_broadcast")

    return udp.normalize_ssid(value)
