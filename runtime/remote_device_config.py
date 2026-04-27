"""
Apply remote device configuration to local machine state.

Extracted from main for testing and to keep the composition root thinner.
"""

from __future__ import annotations

import logging
import os
import time
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from domain.app_context import AppContext
from domain.game_remote_sync import (
    are_remote_playing_games_cleared,
    handle_incoming_game_status,
)

_log = logging.getLogger(__name__)


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


def are_remote_gate_stable_games(games_cfg: list[dict[str, Any]]) -> bool:
    """True when all games are in stable startup statuses: ready/downloading."""
    if not isinstance(games_cfg, list):
        return False
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        status = str(g.get("status", "")).strip().lower()
        if status not in {"ready", "downloading"}:
            return False
    return True


def _debug_reset_gate_enabled() -> bool:
    return str(os.getenv("DARTSNUT_DEBUG_RESET_GATE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _content_page_uuids_from_runtime_pages(pages: Any) -> list[str]:
    uuids: list[str] = []
    if not isinstance(pages, list):
        return uuids
    for page in pages:
        if not isinstance(page, dict):
            continue
        pu = str(page.get("uuid") or "").strip()
        if not pu or pu == "0":
            continue
        uuids.append(pu)
    return uuids


def _content_page_uuids_from_remote_pages(pages: Any) -> list[str]:
    uuids: list[str] = []
    if not isinstance(pages, list):
        return uuids
    for page in pages:
        if not isinstance(page, dict):
            continue
        pu = str(page.get("uuid") or "").strip()
        if not pu:
            continue
        uuids.append(pu)
    return uuids


@dataclass
class RemoteConfigRuntimeState:
    startup_games_reset_initialized: bool = False
    startup_games_reset_requested_at: Optional[datetime] = None
    awaiting_games_ready_confirmation: bool = False
    startup_games_ready_confirmed_at: Optional[datetime] = None
    startup_filter_playing_until_newer_update: bool = False
    startup_ready_retry_last_at: float = 0.0
    startup_config_refresh_requested: bool = False
    startup_missing_ready_games_recovery_done: bool = False
    startup_firmware_version: Optional[str] = None
    firmware_update_in_progress: bool = False
    remote_downloading_game_ids: set[str] = field(default_factory=set)
    last_applied_pages_updated_at: Optional[datetime] = None
    last_applied_pages_fingerprint: Optional[str] = None
    has_seen_remote_pages_snapshot: bool = False


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
    cancel_game_download: Callable[[str], None]
    local_game_version_matches: Callable[[str, str], bool]
    perform_update: Callable[[], dict]
    get_version: Callable[[], dict]
    is_reset_in_progress: Callable[[], bool]
    on_reset_confirmed: Callable[[], None]
    request_config_refresh: Callable[[], None] = lambda: None
    try_soft_apply_remote_supabase_pages: Optional[
        Callable[[AppContext, List[Dict[str, Any]]], bool]
    ] = None


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

    def _recover_missing_ready_games_on_startup(
        self,
        games_cfg: list[dict[str, Any]],
    ) -> None:
        """
        Startup-only recovery for SSH wipe scenarios:
        remote games can remain `ready` while local ./apps/<id> is missing.
        Trigger local download recovery without changing remote game statuses.
        """
        rt = self._runtime
        if rt.startup_missing_ready_games_recovery_done:
            _log.info("remote config: startup missing-game recovery already complete; skipping")
            return

        deps = self._deps
        had_missing_ready_games = False
        all_missing_ready_games_recovered = True
        for g in games_cfg:
            if not isinstance(g, dict):
                continue
            game_id = str(g.get("id") or "").strip()
            status = str(g.get("status") or "").strip().lower()
            expected_version = str(g.get("version") or "").strip()
            if not game_id or status != "ready":
                continue
            if os.path.isdir(os.path.join(os.getcwd(), "apps", game_id)):
                _log.info(
                    "remote config: startup recovery skip game_id=%s reason=already_present local_version=%s",
                    game_id,
                    expected_version or "(empty)",
                )
                continue
            had_missing_ready_games = True
            _log.info(
                "remote config: startup recovery attempt game_id=%s expected_version=%s",
                game_id,
                expected_version or "(empty)",
            )
            try:
                ok = deps.ensure_game_downloaded(game_id, expected_version)
                if ok:
                    _log.info(
                        "remote config: startup recovery success game_id=%s",
                        game_id,
                    )
                else:
                    all_missing_ready_games_recovered = False
                    _log.warning(
                        "remote config: startup recovery failed game_id=%s (will retry)",
                        game_id,
                    )
            except Exception:
                all_missing_ready_games_recovered = False
                _log.exception(
                    "remote config: startup recovery exception game_id=%s (will retry)",
                    game_id,
                )

        # Keep retrying on subsequent snapshots when startup recovery fails due to
        # transient network/IO issues.
        rt.startup_missing_ready_games_recovery_done = (
            not had_missing_ready_games or all_missing_ready_games_recovered
        )
        if not had_missing_ready_games:
            _log.info(
                "remote config: startup recovery no missing ready games detected; marking complete"
            )
        elif rt.startup_missing_ready_games_recovery_done:
            _log.info("remote config: startup recovery complete")

    def apply(self, config: dict) -> None:
        if not isinstance(config, dict):
            return

        _log.info(
            "remote config: apply snapshot source=%s pages=%s games=%s firmware_update=%s",
            str(config.get("last_update_source", "") or "").strip() or "?",
            isinstance(config.get("pages"), list),
            isinstance(config.get("games"), list),
            bool((config.get("firmware") or {}).get("update"))
            if isinstance(config.get("firmware"), dict)
            else False,
        )

        deps = self._deps
        rt = self._runtime
        ctx = deps.app_ctx
        debug_reset_gate = _debug_reset_gate_enabled()
        # Prefer row-level updated_at from remote sync payloads. device_updated_at is
        # local device state time and may remain stale across remote row updates.
        cfg_ts = parse_iso_ts(config.get("updated_at") or config.get("device_updated_at"))

        if deps.is_reset_in_progress() and is_remote_reset_confirmed(config):
            deps.on_reset_confirmed()

        games_cfg = config.get("games")
        if not rt.startup_games_reset_initialized:
            rt.startup_games_reset_initialized = True
            rt.awaiting_games_ready_confirmation = True
            rt.startup_games_ready_confirmed_at = None
            rt.startup_filter_playing_until_newer_update = False
            rt.startup_games_reset_requested_at = cfg_ts
            rt.startup_ready_retry_last_at = time.time()
            try:
                deps.request_set_all_games_ready()
            except Exception:
                pass
            if not rt.startup_config_refresh_requested:
                rt.startup_config_refresh_requested = True
                try:
                    deps.request_config_refresh()
                except Exception:
                    pass

        if isinstance(games_cfg, list):
            ctx.remote_menu_ready_game_ids = frozenset(
                str(g["id"])
                for g in games_cfg
                if isinstance(g, dict)
                and g.get("id")
                and str(g.get("status", "")).strip().lower() == "ready"
            )
            ctx.reload_game_menu = True
        elif (
            self._runtime.awaiting_games_ready_confirmation
            and str(config.get("last_update_source", "")).strip().lower() == "supabase_bridge"
        ):
            if debug_reset_gate:
                _log.debug(
                    "[reset-gate] pending snapshot without games; source=%r cfg_ts=%r "
                    "baseline_ts=%r awaiting=%s",
                    str(config.get("last_update_source", "")),
                    config.get("device_updated_at") or config.get("updated_at"),
                    rt.startup_games_reset_requested_at,
                    rt.awaiting_games_ready_confirmation,
                )
            # Bridge-originated snapshots may omit `games`; do not auto-confirm reset.
            # Keep waiting for explicit games-state confirmation and periodically
            # re-request all games to be set ready.
            now = time.time()
            if (now - float(self._runtime.startup_ready_retry_last_at)) >= 2.0:
                self._runtime.startup_ready_retry_last_at = now
                try:
                    deps.request_set_all_games_ready()
                except Exception:
                    pass

        service = deps.get_machine_state_service()
        if service is None:
            return

        try:
            pages = config.get("pages")
            if isinstance(pages, list):
                service.set_pages(pages, reload_pages=False)
                source = str(config.get("last_update_source", "") or "").strip().lower()
                pages_updated_at = parse_iso_ts(config.get("pages_updated_at"))
                pages_fingerprint = json.dumps(
                    pages, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                should_reload_pages = False

                if source == "supabase_bridge":
                    if pages_updated_at is not None:
                        baseline = rt.last_applied_pages_updated_at
                        is_newer = baseline is None or pages_updated_at > baseline
                        if is_newer:
                            should_reload_pages = True
                        elif rt.has_seen_remote_pages_snapshot:
                            # If timestamp precision/collision prevents strict "newer",
                            # still reload when payload content actually changed.
                            should_reload_pages = (
                                pages_fingerprint != rt.last_applied_pages_fingerprint
                            )
                        rt.last_applied_pages_updated_at = pages_updated_at
                    elif not rt.has_seen_remote_pages_snapshot:
                        # First bridge snapshot can omit pages_updated_at.
                        # In that case, compare runtime content-page order against
                        # remote content-page order so appended/reordered pages are
                        # not silently cached without applying to UI runtime.
                        runtime_uuids = _content_page_uuids_from_runtime_pages(ctx.pages)
                        remote_uuids = _content_page_uuids_from_remote_pages(pages)
                        should_reload_pages = runtime_uuids != remote_uuids
                    elif rt.has_seen_remote_pages_snapshot:
                        should_reload_pages = (
                            pages_fingerprint != rt.last_applied_pages_fingerprint
                        )
                    rt.has_seen_remote_pages_snapshot = True
                    rt.last_applied_pages_fingerprint = pages_fingerprint

                if should_reload_pages:
                    did_soft = (
                        deps.try_soft_apply_remote_supabase_pages is not None
                        and deps.try_soft_apply_remote_supabase_pages(ctx, pages)
                    )
                    if not did_soft:
                        ctx.reload_pages = True
        except Exception as e:
            _log.error("Error applying remote pages config: %s", e)

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
            _log.warning("Error handling remote bluetooth config: %s", e)

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
                incoming_ids = {
                    str(g.get("id"))
                    for g in games_cfg
                    if isinstance(g, dict) and g.get("id") is not None
                }
                for removed_game_id in tuple(rt.remote_downloading_game_ids - incoming_ids):
                    try:
                        deps.cancel_game_download(removed_game_id)
                    except Exception:
                        pass
                    rt.remote_downloading_game_ids.discard(removed_game_id)
                confirmed_in_this_call = False
                if rt.awaiting_games_ready_confirmation:
                    baseline_ts = rt.startup_games_reset_requested_at
                    has_newer_ts = (
                        baseline_ts is not None and cfg_ts is not None and cfg_ts > baseline_ts
                    )
                    missing_ts_fallback = baseline_ts is None or cfg_ts is None
                    stable_games = are_remote_gate_stable_games(games_cfg)
                    if debug_reset_gate:
                        game_states = []
                        for g in games_cfg:
                            if isinstance(g, dict):
                                game_states.append(
                                    (
                                        str(g.get("id") or ""),
                                        str(g.get("status") or "").strip().lower(),
                                        str(g.get("version") or ""),
                                    )
                                )
                        _log.debug(
                            "[reset-gate] evaluate source=%r cfg_ts=%r parsed_cfg_ts=%r "
                            "baseline_ts=%r stable_games=%s has_newer_ts=%s missing_ts_fallback=%s awaiting=%s "
                            "startup_filter_playing_until_newer_update=%s games=%s",
                            str(config.get("last_update_source", "")),
                            config.get("device_updated_at") or config.get("updated_at"),
                            cfg_ts,
                            baseline_ts,
                            stable_games,
                            has_newer_ts,
                            missing_ts_fallback,
                            rt.awaiting_games_ready_confirmation,
                            rt.startup_filter_playing_until_newer_update,
                            game_states,
                        )
                    if stable_games and (has_newer_ts or missing_ts_fallback):
                        rt.awaiting_games_ready_confirmation = False
                        rt.startup_games_ready_confirmed_at = cfg_ts
                        rt.startup_filter_playing_until_newer_update = True
                        confirmed_in_this_call = True
                        _log.info(
                            "remote config: startup game reset confirmed; enabling remote game commands"
                        )
                        # Run startup missing-game recovery immediately after gate confirmation
                        # so a follow-up snapshot is not required to trigger local downloads.
                        self._recover_missing_ready_games_on_startup(games_cfg)
                        # Defer normal game command handling to the next inbound snapshot.
                        return
                    else:
                        now = time.time()
                        if (now - float(rt.startup_ready_retry_last_at)) >= 2.0:
                            rt.startup_ready_retry_last_at = now
                            try:
                                deps.request_set_all_games_ready()
                            except Exception:
                                pass
                        return
                self._recover_missing_ready_games_on_startup(games_cfg)

                for g in games_cfg:
                    if not isinstance(g, dict):
                        continue
                    game_id = g.get("id")
                    status = str(g.get("status", "")).strip().lower()
                    expected_version = str(g.get("version") or "").strip()
                    if not game_id:
                        continue
                    game_id = str(game_id)
                    if status == "downloading":
                        rt.remote_downloading_game_ids.add(game_id)
                    else:
                        rt.remote_downloading_game_ids.discard(game_id)
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
                            _log.warning(
                                "Error updating remote game status to %s for %s: %s",
                                next_status,
                                gid,
                                e,
                            )

                    def _request_launch(gid: str) -> None:
                        if _current_game_id() != gid:
                            ctx.start_game = True
                            ctx.game_id = gid

                    def _terminate_running_game(gid: str) -> None:
                        if ctx.game and isinstance(ctx.game, dict):
                            running_id = str(ctx.game.get("game_id") or "")
                            if gid and running_id != gid:
                                return
                            if running_id:
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
                    # Continue scanning non-playing entries so install/download commands
                    # are not skipped when a currently playing game appears first.
                    if status == "playing" and ctx.start_game and ctx.game_id == game_id:
                        break
        except Exception as e:
            _log.error("Error applying remote device config: %s", e)

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
                    _log.warning("Error notifying remote sync of startup firmware version: %s", e)

                try:
                    service.set_firmware_info(rt.startup_firmware_version, False)
                except Exception as e:
                    _log.warning("Error persisting startup firmware info locally: %s", e)

                rt.startup_firmware_version = None
        except Exception as e:
            _log.warning("Error handling startup firmware version publish: %s", e)

        try:
            firmware_cfg = config.get("firmware") or {}
            if not isinstance(firmware_cfg, dict):
                return
            if not firmware_cfg.get("update"):
                return
            if rt.firmware_update_in_progress:
                return
            rt.firmware_update_in_progress = True
            _log.info("remote config: firmware update (git) starting")

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
                    _log.warning("Error determining firmware version after update: %s", e)

                payload = {
                    "firmware": {
                        "version": new_version,
                        "update": False,
                    }
                }
                try:
                    deps.publish_partial_state(payload)
                except Exception as e:
                    _log.warning("Error notifying remote sync of firmware update completion: %s", e)

                try:
                    service.set_firmware_info(new_version, False)
                except Exception as e:
                    _log.warning("Error persisting firmware info locally after update: %s", e)
            else:
                _log.error(
                    "Firmware update requested via remote sync but perform_update failed: %s",
                    update_result,
                )
        except Exception as e:
            _log.error("Error handling remote firmware update config: %s", e)
        finally:
            rt.firmware_update_in_progress = False
