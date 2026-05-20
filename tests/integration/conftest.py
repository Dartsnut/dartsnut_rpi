from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from domain.app_context import AppContext
from runtime.remote_device_config import (
    RemoteConfigRuntimeState,
    RemoteDeviceConfigApplier,
    RemoteDeviceConfigDependencies,
)
from runtime.websocket_actions import handle_action_message
from runtime.websocket_ports import (
    BluetoothOpsPort,
    DeviceOpsPort,
    FileOpsPort,
    GitOpsPort,
    JsonOpsPort,
    UserDataOpsPort,
    WebsocketEndpointConfig,
    WebsocketServiceRegistry,
)
from states.game import GameSelectState, InGameState


@dataclass
class FakeMachineStateService:
    brightness: int | None = None
    volume: int | None = None
    pages: list[dict[str, Any]] = field(default_factory=list)
    time_zone: str | None = None
    dim_window: dict[str, Any] = field(default_factory=dict)
    device_name: str = ""
    firmware_info: dict[str, Any] = field(default_factory=dict)
    set_pages_calls: list[tuple[list[dict[str, Any]], bool]] = field(default_factory=list)

    def set_brightness(self, brightness: int) -> None:
        self.brightness = int(brightness)

    def set_volume(self, volume: int) -> None:
        self.volume = int(volume)

    def set_pages(self, pages: list[dict[str, Any]], reload_pages: bool = False) -> None:
        self.pages = list(pages)
        self.set_pages_calls.append((list(pages), bool(reload_pages)))

    def set_dim_window(self, cfg: dict[str, Any]) -> None:
        self.dim_window = dict(cfg)

    def set_device_name(self, name: str) -> None:
        self.device_name = str(name)

    def set_firmware_info(self, version: str, update: bool) -> None:
        self.firmware_info = {"version": version, "update": bool(update)}


@dataclass
class FakeRemoteSync:
    published: list[dict[str, Any]] = field(default_factory=list)
    connectivity_callback: Callable[[bool], None] | None = None
    _reload_config: Callable[[], None] | None = None
    _on_config_updated: Callable[[dict[str, Any]], None] | None = None

    def publish_partial_state(self, payload: dict[str, Any]) -> None:
        self.published.append(dict(payload))

    def request_set_game_status(self, game_id: str, status: str) -> None:
        self.published.append({"games": [{"id": game_id, "status": status}]})

    def request_set_all_games_ready(self) -> None:
        self.published.append({"games_reset": True})

    def request_device_reset_state(self) -> None:
        self.published.append(
            {
                "ip_address": "",
                "ssid": "",
                "pages": [],
                "games": [],
                "dim_window": {"dim_window_enabled": False},
            }
        )

    def is_connected(self) -> bool:
        return True

    def set_connectivity_callback(self, callback: Callable[[bool], None] | None) -> None:
        self.connectivity_callback = callback

    def start_sync_if_available(
        self,
        device_info: dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[dict[str, Any]], None],
    ) -> None:
        self._reload_config = reload_config
        self._on_config_updated = on_config_updated

    def restart_sync(
        self,
        device_info: dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[dict[str, Any]], None],
    ) -> None:
        self.start_sync_if_available(device_info, reload_config, on_config_updated)

    def is_bridge_active(self) -> bool:
        return True

    def emit_config(self, config: dict[str, Any]) -> None:
        if self._on_config_updated:
            self._on_config_updated(dict(config))
        if self._reload_config:
            self._reload_config()


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir(parents=True, exist_ok=True)
    (tmp_path / "apps" / "conf.json").write_text(
        json.dumps({"pages": [], "pages_updated_at": ""}), encoding="utf-8"
    )
    (tmp_path / "device.json").write_text(
        json.dumps(
            {
                "id": "AA:BB:CC:DD:EE:FF",
                "brightness": "50",
                "volume": "50",
                "name": "Board",
                "updated_at": "2026-03-30T00:00:00",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def machine_state() -> FakeMachineStateService:
    return FakeMachineStateService()


@pytest.fixture()
def app_ctx() -> AppContext:
    return AppContext(
        display=object(),
        assets=object(),
        get_device_info=lambda: {},
        set_brightness=lambda _v: None,
        set_volume=lambda _v: None,
    )


@pytest.fixture()
def fake_remote_sync() -> FakeRemoteSync:
    return FakeRemoteSync()


@pytest.fixture()
def websocket_registry() -> WebsocketServiceRegistry:
    return WebsocketServiceRegistry(
        file_ops=FileOpsPort(
            receive_file=lambda *_a, **_k: {"action": "send_file", "message": "ok"},
            send_file=lambda *_a, **_k: {"action": "get_file", "message": "ok"},
            remove_directory=lambda *_a, **_k: {"action": "remove_directory", "message": "ok"},
            get_file_md5=lambda *_a, **_k: {"action": "get_file_md5", "message": "ok"},
            get_file_list=lambda *_a, **_k: {"action": "list_files", "message": "ok"},
            create_directory=lambda *_a, **_k: {"action": "create_directory", "message": "ok"},
            download_app=lambda *_a, **_k: {"action": "download_app", "message": "ok"},
            get_app_list=lambda *_a, **_k: {"action": "list_apps", "message": "ok"},
            start_game_download_async=lambda *_a, **_k: {"action": "download_app", "message": "ok"},
            start_game_download_async_with_url=lambda *_a, **_k: {"action": "download_app", "message": "ok"},
            get_download_progress=lambda *_a, **_k: {"action": "get_download_progress", "message": "ok"},
        ),
        json_ops=JsonOpsPort(
            read_json_file=lambda *_a, **_k: {"action": "read_json", "message": "ok"},
            write_json_file=lambda *_a, **_k: {"action": "write_json", "message": "ok"},
            get_device_info=lambda *_a, **_k: {"action": "get_device_info", "message": "ok"},
            set_device_name=lambda *_a, **_k: {"action": "set_device_name", "message": "ok"},
        ),
        bluetooth_ops=BluetoothOpsPort(
            scan_bluetooth_devices=lambda *_a, **_k: {"action": "bluetooth_scan", "message": "ok"},
            list_paired_devices=lambda *_a, **_k: {"action": "bluetooth_list", "message": "ok"},
            disconnect_and_unpair_device=lambda *_a, **_k: {"action": "bluetooth_remove", "message": "ok"},
            pair_and_connect_device=lambda *_a, **_k: {"action": "bluetooth_connect", "message": "ok"},
        ),
        git_ops=GitOpsPort(
            check_update=lambda *_a, **_k: {"action": "check_update", "message": "ok"},
            perform_update=lambda *_a, **_k: {"action": "perform_update", "message": "ok"},
            get_version=lambda *_a, **_k: {"action": "get_version", "message": "ok"},
        ),
        device_ops=DeviceOpsPort(
            get_wifi_rssi=lambda *_a, **_k: {"action": "get_wifi_rssi", "message": "ok"},
            forget_wifi=lambda *_a, **_k: None,
            reboot=lambda *_a, **_k: None,
            get_ssh_status=lambda *_a, **_k: {"action": "get_ssh_status", "message": "ok"},
            start_ssh=lambda *_a, **_k: {"action": "start_ssh", "message": "ok"},
            stop_ssh=lambda *_a, **_k: {"action": "stop_ssh", "message": "ok"},
            get_brightness=lambda *_a, **_k: {"action": "get_brightness", "brightness": 50},
            get_volume=lambda *_a, **_k: {"action": "get_volume", "volume": 50},
            get_dim_window=lambda *_a, **_k: {"action": "get_dim_window", "dim_window_enabled": False},
            set_dim_window=lambda *_a, **_k: {"action": "set_dim_window", "message": "Success"},
        ),
        user_data_ops=UserDataOpsPort(
            get_user_data=lambda *_a, **_k: {"action": "get_user_data", "message": "ok"},
            update_user_info=lambda *_a, **_k: {"action": "update_user_info", "message": "ok"},
            get_game_playtime=lambda *_a, **_k: {"action": "get_game_playtime", "message": "ok"},
        ),
    )


@pytest.fixture()
def endpoint_state() -> dict[str, Any]:
    return {
        "brightness": None,
        "volume": None,
        "time_zone": None,
        "located": False,
        "reloaded": False,
        "dim_check": False,
        "started_game": None,
    }


@pytest.fixture()
def endpoint_config(endpoint_state: dict[str, Any]) -> WebsocketEndpointConfig:
    return WebsocketEndpointConfig(
        set_brightness=lambda v: endpoint_state.__setitem__("brightness", int(v)),
        set_volume=lambda v: endpoint_state.__setitem__("volume", int(v)),
        set_time_zone=lambda tz: endpoint_state.__setitem__("time_zone", str(tz)),
        locate_device=lambda: endpoint_state.__setitem__("located", True),
        reload_config=lambda: endpoint_state.__setitem__("reloaded", True),
        trigger_dim_check=lambda: endpoint_state.__setitem__("dim_check", True),
        start_game_process=lambda game_id: endpoint_state.__setitem__("started_game", game_id)
        or True,
    )


@pytest.fixture()
def run_action() -> Callable[..., dict[str, Any]]:
    def _run(
        *,
        message: dict[str, Any],
        registry: WebsocketServiceRegistry,
        endpoint_config: WebsocketEndpointConfig,
    ) -> dict[str, Any]:
        sent: list[tuple[Any, dict[str, Any]]] = []

        async def send_response(req_id: Any, data: dict[str, Any]) -> None:
            sent.append((req_id, data))

        asyncio.run(
            handle_action_message(
                websocket=object(),
                message=message,
                registry=registry,
                endpoint_config=endpoint_config,
                send_response=send_response,
            )
        )
        if not sent:
            return {"req_id": message.get("req_id"), "payload": {}}
        req_id, payload = sent[-1]
        return {"req_id": req_id, "payload": payload}

    return _run


@pytest.fixture()
def remote_config_harness(
    app_ctx: AppContext, machine_state: FakeMachineStateService
) -> dict[str, Any]:
    events: dict[str, Any] = {
        "reload_called": False,
        "published": [],
        "status_updates": [],
        "ensure_download_calls": [],
        "cancel_download_calls": [],
        "ensure_download_result": True,
        "all_ready_requests": 0,
        "reset_confirmed": False,
        "tz": None,
        "term_calls": 0,
    }

    deps = RemoteDeviceConfigDependencies(
        app_ctx=app_ctx,
        get_machine_state_service=lambda: machine_state,
        bluetooth_scan_controller=type(
            "FakeBle",
            (),
            {
                "start_scan_if_requested": lambda self: None,
                "start_connect_if_requested": lambda self, _a: None,
            },
        )(),
        publish_partial_state=lambda payload: events["published"].append(dict(payload)),
        request_set_game_status=lambda gid, st: events["status_updates"].append((gid, st)),
        set_time_zone=lambda tz: events.__setitem__("tz", tz),
        term_game_process=lambda _g: events.__setitem__("term_calls", events["term_calls"] + 1),
        ensure_game_downloaded=lambda gid, ver: events["ensure_download_calls"].append((gid, ver))
        or bool(events["ensure_download_result"]),
        cancel_game_download=lambda gid: events["cancel_download_calls"].append(gid),
        local_game_version_matches=lambda *_a: False,
        perform_update=lambda: {"error": False},
        get_version=lambda: {"error": False, "version": "9.9.9"},
        is_reset_in_progress=lambda: False,
        on_reset_confirmed=lambda: events.__setitem__("reset_confirmed", True),
    )
    runtime = RemoteConfigRuntimeState(startup_firmware_version="1.0.0")
    applier = RemoteDeviceConfigApplier(deps, runtime)

    def apply(config: dict[str, Any]) -> None:
        # Model the Supabase inbound callback path used for external changes.
        applier.apply(config)
        events["reload_called"] = True

    return {
        "apply": apply,
        "events": events,
        "deps": deps,
        "runtime": runtime,
        "machine_state": machine_state,
        "app_ctx": app_ctx,
    }


@dataclass
class FakeGameProcess:
    running: bool = True
    signals: list[Any] = field(default_factory=list)

    def poll(self):
        return None if self.running else 1

    def send_signal(self, sig: Any) -> None:
        self.signals.append(sig)


class FakeDisplay:
    def __init__(self) -> None:
        self.frames: list[Any] = []

    def update_frame_buffer(self, frame: Any) -> None:
        self.frames.append(frame)


class FakeAssets:
    def __init__(self) -> None:
        self.qrcode_image = object()
        self.game_select_image = object()
        self.font24 = None


@pytest.fixture()
def game_sim_harness(fake_remote_sync):
    transitions: list[str] = []
    widget_terminated: list[bool] = []
    game_terminated: list[str] = []
    process = FakeGameProcess()
    ctx = AppContext(
        display=FakeDisplay(),
        assets=FakeAssets(),
        get_device_info=lambda: {"model": "PixelDart"},
        set_brightness=lambda _v: None,
        set_volume=lambda _v: None,
    )
    ctx.page_tick = 0.0
    ctx.pages = [{"uuid": "p1"}]
    ctx.current_button_state = {}
    ctx.game_list = [{"id": "chess", "preview": [bytearray(128 * 128 * 3)]}]
    ctx.game_index = 0
    ctx.game_preview_index = 0
    ctx.term_widget_processes = lambda _pages: widget_terminated.append(True)
    ctx.term_game_process = lambda game: game_terminated.append(str(game.get("game_id", "")))
    ctx.start_game_process = (
        lambda gid: {
            "process": process,
            "shm": None,
            "game_id": gid,
            "launched": False,
            "pico8_first_frame_seen": False,
        }
    )
    ctx.set_game_status = fake_remote_sync.request_set_game_status
    ctx.transition_to = lambda state: transitions.append(state.name())

    return {
        "ctx": ctx,
        "process": process,
        "transitions": transitions,
        "widget_terminated": widget_terminated,
        "game_terminated": game_terminated,
        "select_state": GameSelectState(),
        "in_game_state": InGameState(),
        "remote_sync": fake_remote_sync,
    }
