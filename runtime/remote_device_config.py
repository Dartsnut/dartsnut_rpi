"""
Apply remote device configuration to local machine state.

Extracted from main for testing and to keep the composition root thinner.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from domain.app_context import AppContext
from domain.game_firestore_sync import (
    are_firestore_playing_games_cleared,
    handle_incoming_game_status,
)


def parse_iso_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1]
            return datetime.fromisoformat(s)
    except Exception:
        return None
    return None


def is_remote_reset_confirmed(config: dict) -> bool:
    if not isinstance(config, dict):
        return False
    if config.get("ip_address") != "":
        return False
    if "ssid" in config and config.get("ssid") != "":
        return False
    pages = config.get("pages")
    if pages is not None and (not isinstance(pages, list) or len(pages) != 0):
        return False
    games = config.get("games")
    if games is not None and (not isinstance(games, list) or len(games) != 0):
        return False
    dim_window = config.get("dim_window")
    if not isinstance(dim_window, dict):
        return False
    return bool(dim_window.get("dim_window_enabled")) is False


def is_firestore_reset_confirmed(config: dict) -> bool:
    """Backward-compatible alias for old test and call sites."""
    return is_remote_reset_confirmed(config)


@dataclass
class RemoteConfigRuntimeState:
    awaiting_games_ready_confirmation: bool = False
    startup_games_ready_confirmed_at: Optional[datetime] = None
    startup_filter_playing_until_newer_update: bool = False
    startup_ready_retry_last_at: float = 0.0
    startup_firmware_version: Optional[str] = None
    firmware_update_in_progress: bool = False


@dataclass
class RemoteDeviceConfigDependencies:
    app_ctx: AppContext
    get_machine_state_service: Callable[[], Any]
    bluetooth_scan_controller: Any
    publish_partial_state: Callable[[dict], None]
    request_set_game_status: Callable[[str, str], None]
    request_set_all_games_ready: Callable[[], None]
    set_time_zone: Callable[[str], Any]
    term_game_process: Callable[[Any], None]
    ensure_game_downloaded: Callable[[str, str], bool]
    local_game_version_matches: Callable[[str, str], bool]
    perform_update: Callable[[], dict]
    get_version: Callable[[], dict]
    is_reset_in_progress: Callable[[], bool]
    on_reset_confirmed: Callable[[], None]


class RemoteDeviceConfigApplier:
    """Applies one remote config snapshot from the sync bridge."""

    def __init__(
        self,
        deps: RemoteDeviceConfigDependencies,
        runtime: RemoteConfigRuntimeState,
    ) -> None:
        self._deps = deps
        self._runtime = runtime

    @property
    def runtime(self) -> RemoteConfigRuntimeState:
        return self._runtime

    def apply(self, config: dict) -> None:
        if not isinstance(config, dict):
            return

        deps = self._deps
        rt = self._runtime
        ctx = deps.app_ctx

        if deps.is_reset_in_progress() and is_remote_reset_confirmed(config):
            deps.on_reset_confirmed()

        games_cfg = config.get("games")
        if isinstance(games_cfg, list):
            ctx.firestore_menu_ready_game_ids = frozenset(
                str(g["id"])
                for g in games_cfg
                if isinstance(g, dict)
                and g.get("id")
                and str(g.get("status", "")).strip().lower() == "ready"
            )

        service = deps.get_machine_state_service()
        if service is None:
            return

        try:
            pages = config.get("pages")
            if isinstance(pages, list):
                service.set_pages(pages, reload_pages=False)
                ctx.reload_pages = True
        except Exception as e:
            print(f"Error applying remote pages config: {e}")

        try:
            bluetooth_cfg = config.get("bluetooth")
            if isinstance(bluetooth_cfg, dict):
                if bool(bluetooth_cfg.get("is_scan")):
                    deps.bluetooth_scan_controller.start_scan_if_requested()
                connect_address = bluetooth_cfg.get("connect")
                if isinstance(connect_address, str) and connect_address.strip():
                    deps.bluetooth_scan_controller.start_connect_if_requested(
                        connect_address.strip()
                    )
        except Exception as e:
            print(f"Error handling remote bluetooth config: {e}")

        try:
            if "brightness" in config or "Brightness" in config:
                try:
                    key = "brightness" if "brightness" in config else "Brightness"
                    brightness_val = int(config.get(key))
                    service.set_brightness(brightness_val)
                except Exception:
                    pass

            if "volume" in config:
                try:
                    volume_val = int(config.get("volume"))
                    service.set_volume(volume_val)
                except Exception:
                    pass

            if "time_zone" in config:
                tz = config.get("time_zone")
                if tz:
                    deps.set_time_zone(tz)

            dim_window = config.get("dim_window") or {}
            if isinstance(dim_window, dict):
                dim_cfg = {
                    "dim_window_enabled": dim_window.get("dim_window_enabled"),
                    "dim_window_start": dim_window.get("dim_window_start"),
                    "dim_window_end": dim_window.get("dim_window_end"),
                    "dim_level": dim_window.get("dim_level"),
                    "dim_restore_seconds": dim_window.get("dim_restore_seconds"),
                }
                service.set_dim_window(dim_cfg)

            device_info = config.get("device_info") or {}
            if isinstance(device_info, dict) and "name" in device_info:
                service.set_device_name(device_info.get("name", ""))

            games_cfg = config.get("games")
            if isinstance(games_cfg, list):
                cfg_ts = parse_iso_ts(
                    config.get("device_updated_at") or config.get("updated_at")
                )
                confirmed_in_this_call = False
                if rt.awaiting_games_ready_confirmation:
                    if are_firestore_playing_games_cleared(games_cfg):
                        rt.awaiting_games_ready_confirmation = False
                        rt.startup_games_ready_confirmed_at = cfg_ts
                        rt.startup_filter_playing_until_newer_update = True
                        confirmed_in_this_call = True
                        print(
                            "Remote game reset confirmed; enabling game command handling"
                        )
                    else:
                        now = time.time()
                        if (now - float(rt.startup_ready_retry_last_at)) >= 2.0:
                            rt.startup_ready_retry_last_at = now
                            try:
                                deps.request_set_all_games_ready()
                            except Exception:
                                pass
                        return

                for g in games_cfg:
                    if not isinstance(g, dict):
                        continue
                    game_id = g.get("id")
                    status = str(g.get("status", "")).strip().lower()
                    expected_version = str(g.get("version") or "").strip()
                    if not game_id:
                        continue
                    game_id = str(game_id)
                    if (
                        rt.startup_filter_playing_until_newer_update
                        and not confirmed_in_this_call
                        and status != "playing"
                    ):
                        rt.startup_filter_playing_until_newer_update = False
                    if status == "playing" and rt.startup_filter_playing_until_newer_update:
                        if (
                            rt.startup_games_ready_confirmed_at is not None
                            and cfg_ts is not None
                            and cfg_ts <= rt.startup_games_ready_confirmed_at
                        ):
                            continue
                        rt.startup_filter_playing_until_newer_update = False

                    def _current_game_id() -> str:
                        if ctx.game and isinstance(ctx.game, dict):
                            return str(ctx.game.get("game_id") or "")
                        return ""

                    def _game_exists(gid: str) -> bool:
                        return os.path.isdir(os.path.join(os.getcwd(), "apps", gid))

                    def _set_status(gid: str, next_status: str) -> None:
                        try:
                            deps.request_set_game_status(gid, next_status)
                        except Exception as e:
                            print(
                                f"Error updating remote game status to {next_status} for {gid}: {e}"
                            )

                    def _request_launch(gid: str) -> None:
                        if _current_game_id() != gid:
                            ctx.start_game = True
                            ctx.game_id = gid

                    def _terminate_running_game(gid: str) -> None:
                        if ctx.game and isinstance(ctx.game, dict):
                            running_id = str(ctx.game.get("game_id") or "")
                            if running_id == gid:
                                deps.term_game_process(ctx.game)
                                ctx.game = None
                                ctx.reload_conf = True

                    if status == "downloading" and deps.local_game_version_matches(
                        game_id, expected_version
                    ):
                        _set_status(game_id, "ready")
                        continue

                    handle_incoming_game_status(
                        game_id,
                        status,
                        expected_version=expected_version,
                        current_game_id=_current_game_id(),
                        game_exists=_game_exists,
                        ensure_game_downloaded=deps.ensure_game_downloaded,
                        set_game_status=_set_status,
                        request_launch=_request_launch,
                        terminate_running_game=_terminate_running_game,
                    )
                    if status == "playing":
                        break
        except Exception as e:
            print(f"Error applying remote device config: {e}")

        try:
            if rt.startup_firmware_version:
                payload = {
                    "firmware": {
                        "version": rt.startup_firmware_version,
                        "update": False,
                    }
                }
                try:
                    deps.publish_partial_state(payload)
                except Exception as e:
                    print(f"Error notifying remote sync of startup firmware version: {e}")

                try:
                    service.set_firmware_info(rt.startup_firmware_version, False)
                except Exception as e:
                    print(f"Error persisting startup firmware info locally: {e}")

                rt.startup_firmware_version = None
        except Exception as e:
            print(f"Error handling startup firmware version publish: {e}")

        try:
            firmware_cfg = config.get("firmware") or {}
            if not isinstance(firmware_cfg, dict):
                return
            if not firmware_cfg.get("update"):
                return
            if rt.firmware_update_in_progress:
                return
            rt.firmware_update_in_progress = True

            update_result = deps.perform_update()
            if isinstance(update_result, dict) and not update_result.get("error"):
                new_version = "dev"
                try:
                    version_result = deps.get_version()
                    if (
                        isinstance(version_result, dict)
                        and not version_result.get("error")
                        and version_result.get("version")
                    ):
                        new_version = str(version_result.get("version"))
                except Exception as e:
                    print(f"Error determining firmware version after update: {e}")

                payload = {
                    "firmware": {
                        "version": new_version,
                        "update": False,
                    }
                }
                try:
                    deps.publish_partial_state(payload)
                except Exception as e:
                    print(f"Error notifying Firestore of firmware update completion: {e}")

                try:
                    service.set_firmware_info(new_version, False)
                except Exception as e:
                    print(f"Error persisting firmware info locally after update: {e}")
            else:
                print(f"Firmware update requested via remote sync but perform_update failed: {update_result}")
        except Exception as e:
            print(f"Error handling remote firmware update config: {e}")
        finally:
            rt.firmware_update_in_progress = False
