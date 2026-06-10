"""
Apply remote device configuration to local machine state.

Extracted from main for testing and to keep the composition root thinner.
"""

from __future__ import annotations

import logging
import os
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from domain.app_context import AppContext
from domain.game_remote_sync import handle_incoming_game_status

_log = logging.getLogger(__name__)

_DUPLICATE_SNAPSHOT_WINDOW_SECONDS = 2.0
_BRIDGE_SOURCES = frozenset({"supabase_bridge", "supabase_bridge_init"})


def _normalize_utc_naive(dt: datetime) -> datetime:
    """Return a naive UTC datetime safe for comparisons with _utc_now()."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_iso_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return _normalize_utc_naive(datetime.fromisoformat(s))
        if isinstance(value, datetime):
            return _normalize_utc_naive(value)
    except Exception:
        return None
    return None


def resolve_snapshot_updated_at(config: Any) -> Optional[datetime]:
    """Use the newer of row updated_at and state.device_updated_at."""
    if not isinstance(config, dict):
        return None
    row_at = parse_iso_ts(config.get("updated_at"))
    device_at = parse_iso_ts(config.get("device_updated_at"))
    if row_at is None:
        return device_at
    if device_at is None:
        return row_at
    return row_at if row_at > device_at else device_at


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


def is_remote_reset_confirmation_source(value: Any) -> bool:
    source = str(value or "").strip().lower()
    # Accept both while rolling out source-tagged reset confirmations.
    return source in {"supabase_bridge_init", "supabase_bridge"}


def normalize_games_list(config: dict) -> list:
    """Return config games as a list; missing/null/non-list becomes []."""
    games = config.get("games") if isinstance(config, dict) else None
    return games if isinstance(games, list) else []


def games_settlement_patch_entries(games_cfg: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one-shot ready patches for games not in ready/downloading."""
    patch: list[dict[str, Any]] = []
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        game_id = str(g.get("id") or "").strip()
        if not game_id:
            continue
        status = str(g.get("status", "")).strip().lower()
        if status in {"ready", "downloading"}:
            continue
        entry: dict[str, Any] = {"id": game_id, "status": "ready"}
        version = str(g.get("version") or "").strip()
        if version:
            entry["version"] = version
        patch.append(entry)
    return patch


def games_cfg_for_startup_recovery(
    games_cfg: list[dict[str, Any]], *, after_settlement: bool
) -> list[dict[str, Any]]:
    """
    Games list for missing-local recovery.

    On the settlement snapshot, inbound rows may still show `playing` while
    settlement already published `ready`. Treat those as ready for recovery.
    """
    out: list[dict[str, Any]] = []
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        entry = dict(g)
        if after_settlement:
            status = str(entry.get("status") or "").strip().lower()
            if status not in {"ready", "downloading"}:
                entry["status"] = "ready"
        out.append(entry)
    return out


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def snapshot_dedupe_fingerprint(config: dict) -> str:
    """Stable fingerprint for duplicate remote snapshot suppression."""
    from runtime.sync.fingerprint import snapshot_content_fingerprint

    return snapshot_content_fingerprint(config)


_LOCAL_SETTING_GUARD_SECONDS = 5.0


def note_local_setting_change(
    runtime: "RemoteConfigRuntimeState",
    key: str,
    value: int,
    *,
    at: Optional[datetime] = None,
) -> None:
    """Record a local UI-driven brightness/volume change for remote echo guards."""
    setting_key = str(key or "").strip().lower()
    if setting_key not in {"brightness", "volume"}:
        return
    runtime.local_setting_value[setting_key] = int(value)
    runtime.local_setting_changed_at[setting_key] = (
        _normalize_utc_naive(at) if at is not None else _utc_now()
    )


def should_accept_remote_setting(
    runtime: "RemoteConfigRuntimeState",
    key: str,
    remote_value: int,
    cfg_ts: Optional[datetime],
    *,
    source: str = "",
) -> bool:
    """Reject stale bridge brightness/volume while a recent local UI edit is in flight."""
    setting_key = str(key or "").strip().lower()
    if setting_key not in {"brightness", "volume"}:
        return True
    try:
        remote_int = int(remote_value)
    except (TypeError, ValueError):
        return True

    local_at = runtime.local_setting_changed_at.get(setting_key)
    local_val = runtime.local_setting_value.get(setting_key)
    if local_at is None or local_val is None:
        return True
    if remote_int == int(local_val):
        return True

    age = (_utc_now() - local_at).total_seconds()
    if age > _LOCAL_SETTING_GUARD_SECONDS:
        return True

    src = str(source or "").strip().lower()
    if src in _BRIDGE_SOURCES:
        return False
    if cfg_ts is not None and cfg_ts <= local_at:
        return False
    return True


def note_local_game_transition(
    runtime: "RemoteConfigRuntimeState",
    game_id: str,
    status: str,
    *,
    at: Optional[datetime] = None,
) -> None:
    """Record a local UI-driven game status transition for monotonic guards."""
    gid = str(game_id or "").strip()
    if not gid:
        return
    normalized = str(status or "").strip().lower()
    if normalized not in {"ready", "playing"}:
        return
    runtime.local_game_transition_at[gid] = (
        _normalize_utc_naive(at) if at is not None else _utc_now()
    )


def should_accept_remote_playing_command(
    runtime: "RemoteConfigRuntimeState",
    game_id: str,
    cfg_ts: Optional[datetime],
    *,
    source: str = "",
) -> bool:
    """
    True when a remote `playing` command should be honored.

    Requires remote updated_at to be strictly newer than the last local transition
    for that game, and not a replay of an already-processed playing command.
    """
    gid = str(game_id or "").strip()
    if not gid:
        return False
    src = str(source or "").strip().lower()

    local_at = runtime.local_game_transition_at.get(gid)
    if local_at is not None:
        if cfg_ts is None:
            if src in _BRIDGE_SOURCES:
                return False
        elif cfg_ts <= local_at:
            return False

    processed_at = runtime.last_processed_remote_playing_at.get(gid)
    if processed_at is not None and cfg_ts is not None and cfg_ts <= processed_at:
        return False

    return True


def record_remote_playing_accepted(
    runtime: "RemoteConfigRuntimeState",
    game_id: str,
    cfg_ts: Optional[datetime],
) -> None:
    gid = str(game_id or "").strip()
    if not gid:
        return
    if cfg_ts is not None:
        runtime.last_processed_remote_playing_at[gid] = _normalize_utc_naive(cfg_ts)


def should_skip_duplicate_remote_snapshot(
    runtime: "RemoteConfigRuntimeState", config: dict
) -> bool:
    """Suppress repeated identical bridge snapshots in a short window."""
    source = str(config.get("last_update_source") or "").strip().lower()
    if source not in _BRIDGE_SOURCES:
        return False
    fp = snapshot_dedupe_fingerprint(config)
    now = _utc_now()
    if (
        runtime.last_remote_snapshot_fingerprint == fp
        and runtime.last_remote_snapshot_applied_at is not None
        and (now - runtime.last_remote_snapshot_applied_at).total_seconds()
        < _DUPLICATE_SNAPSHOT_WINDOW_SECONDS
    ):
        return True
    runtime.last_remote_snapshot_fingerprint = fp
    runtime.last_remote_snapshot_applied_at = now
    return False


def are_remote_gate_stable_games(games_cfg: list[dict[str, Any]]) -> bool:
    """True when all games are in stable startup statuses: ready/downloading."""
    for g in games_cfg:
        if not isinstance(g, dict):
            continue
        status = str(g.get("status", "")).strip().lower()
        if status not in {"ready", "downloading"}:
            return False
    return True


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
    awaiting_games_ready_confirmation: bool = False
    startup_settlement_completed: bool = False
    startup_games_ready_confirmed_at: Optional[datetime] = None
    startup_filter_playing_until_newer_update: bool = False
    startup_missing_ready_games_recovery_done: bool = False
    startup_firmware_version: Optional[str] = None
    firmware_update_in_progress: bool = False
    remote_downloading_game_ids: set[str] = field(default_factory=set)
    last_applied_pages_updated_at: Optional[datetime] = None
    last_applied_pages_fingerprint: Optional[str] = None
    has_seen_remote_pages_snapshot: bool = False
    last_applied_non_bridge_pages_fingerprint: Optional[str] = None
    last_remote_controller_macs: set[str] = field(default_factory=set)
    local_game_transition_at: Dict[str, datetime] = field(default_factory=dict)
    last_processed_remote_playing_at: Dict[str, datetime] = field(default_factory=dict)
    last_remote_snapshot_fingerprint: Optional[str] = None
    last_remote_snapshot_applied_at: Optional[datetime] = None
    local_setting_changed_at: Dict[str, datetime] = field(default_factory=dict)
    local_setting_value: Dict[str, int] = field(default_factory=dict)


@dataclass
class RemoteDeviceConfigDependencies:
    app_ctx: AppContext
    get_machine_state_service: Callable[[], Any]
    bluetooth_scan_controller: Any
    publish_partial_state: Callable[[dict], None]
    request_set_game_status: Callable[[str, str], None]
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
    disconnect_and_unpair_device: Callable[[str], dict] = lambda _mac: {}
    remove_local_game_folder: Callable[[str], bool] = lambda _gid: False
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

    def _reconcile_local_game_folders(self, games_cfg: list[dict[str, Any]]) -> None:
        """Make local installed game folders match remote game membership."""
        try:
            from game_lifecycle import local_game_index
        except Exception:
            return

        remote_ids = {
            str(g.get("id") or "").strip()
            for g in games_cfg
            if isinstance(g, dict) and str(g.get("id") or "").strip()
        }
        try:
            local_ids = set(local_game_index())
        except Exception as e:
            _log.warning("remote config: failed to index local games for reconcile: %s", e)
            return

        deps = self._deps
        ctx = deps.app_ctx
        rt = self._runtime
        removed_any = False
        for game_id in sorted(local_ids - remote_ids):
            if game_id in rt.remote_downloading_game_ids:
                try:
                    deps.cancel_game_download(game_id)
                except Exception:
                    pass
                rt.remote_downloading_game_ids.discard(game_id)

            if ctx.game and isinstance(ctx.game, dict):
                running_id = str(ctx.game.get("game_id") or "")
                if running_id == game_id:
                    try:
                        deps.term_game_process(ctx.game)
                    except Exception as e:
                        _log.warning(
                            "remote config: failed to terminate removed game_id=%s: %s",
                            game_id,
                            e,
                        )
                    ctx.game = None
                    ctx.reload_conf = True

            try:
                if deps.remove_local_game_folder(game_id):
                    removed_any = True
                    _log.info("remote config: removed local game folder game_id=%s", game_id)
            except Exception as e:
                _log.warning(
                    "remote config: failed to remove local game folder game_id=%s: %s",
                    game_id,
                    e,
                )

        if removed_any:
            ctx.reload_game_menu = True

    def _reconcile_downloading_games(self, games_cfg: list[dict[str, Any]]) -> None:
        """
        Resume or clear remote ``downloading`` after interrupted downloads.

        Startup settlement intentionally leaves existing ``downloading`` rows
        untouched, and the first post-restart snapshot may skip game commands.

        Uses async downloads to avoid blocking the main thread.
        """
        from game_lifecycle import download_game_async

        deps = self._deps
        rt = self._runtime

        for g in games_cfg:
            if not isinstance(g, dict):
                continue
            game_id = str(g.get("id") or "").strip()
            if not game_id:
                continue
            if str(g.get("status") or "").strip().lower() != "downloading":
                continue
            expected_version = str(g.get("version") or "").strip()
            rt.remote_downloading_game_ids.add(game_id)

            if deps.local_game_version_matches(game_id, expected_version):
                _log.info(
                    "remote config: reconcile downloading->ready game_id=%s reason=local_version_matches",
                    game_id,
                )
                try:
                    deps.request_set_game_status(game_id, "ready")
                except Exception as e:
                    _log.warning(
                        "remote config: reconcile ready publish failed game_id=%s: %s",
                        game_id,
                        e,
                    )
                rt.remote_downloading_game_ids.discard(game_id)
                continue

            # Start async download instead of blocking
            def on_success(gid: str) -> None:
                _log.info(
                    "remote config: reconcile downloading->ready game_id=%s reason=download_complete",
                    gid,
                )
                try:
                    deps.request_set_game_status(gid, "ready")
                except Exception as e:
                    _log.warning(
                        "remote config: reconcile ready publish failed game_id=%s: %s",
                        gid,
                        e,
                    )
                rt.remote_downloading_game_ids.discard(gid)

            def on_failure(gid: str, error: str) -> None:
                _log.warning(
                    "remote config: reconcile download failed game_id=%s error=%s",
                    gid,
                    error,
                )
                rt.remote_downloading_game_ids.discard(gid)

            started = download_game_async(
                game_id,
                expected_version,
                on_success=on_success,
                on_failure=on_failure,
            )
            if not started:
                _log.debug(
                    "remote config: reconcile download already in progress game_id=%s",
                    game_id,
                )

    def _publish_startup_recovery_game_status(
        self,
        game_id: str,
        expected_version: str,
        status: str,
    ) -> None:
        """Notify Supabase clients before/during startup recovery downloads."""
        try:
            self._deps.publish_partial_state(
                {
                    "games": [
                        {
                            "id": game_id,
                            "version": expected_version,
                            "status": status,
                        }
                    ]
                }
            )
        except Exception as e:
            _log.warning(
                "remote config: startup recovery publish failed game_id=%s status=%s: %s",
                game_id,
                status,
                e,
            )

    def _recover_missing_ready_games_on_startup(
        self,
        games_cfg: list[dict[str, Any]],
    ) -> None:
        """
        Startup-only recovery for SSH wipe scenarios:
        remote games can remain `ready` while local ./apps/<id> is missing.
        Publish `downloading` to remote sync, then fetch the game locally.

        Uses async downloads to avoid blocking the main thread.
        """
        from game_lifecycle import download_game_async

        rt = self._runtime
        if rt.startup_missing_ready_games_recovery_done:
            has_missing_ready_game = False
            for g in games_cfg:
                if not isinstance(g, dict):
                    continue
                game_id = str(g.get("id") or "").strip()
                status = str(g.get("status") or "").strip().lower()
                if not game_id or status != "ready":
                    continue
                if not os.path.isdir(os.path.join(os.getcwd(), "apps", game_id)):
                    has_missing_ready_game = True
                    break
            if not has_missing_ready_game:
                return
            rt.startup_missing_ready_games_recovery_done = False

        deps = self._deps
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

            _log.info(
                "remote config: startup recovery attempt game_id=%s expected_version=%s",
                game_id,
                expected_version or "(empty)",
            )
            self._publish_startup_recovery_game_status(
                game_id, expected_version, "downloading"
            )
            rt.remote_downloading_game_ids.add(game_id)

            # Start async download instead of blocking
            def make_callbacks(gid: str, gver: str):
                def on_success(game_id: str) -> None:
                    _log.info(
                        "remote config: startup recovery success game_id=%s",
                        game_id,
                    )
                    self._publish_startup_recovery_game_status(game_id, gver, "ready")
                    rt.remote_downloading_game_ids.discard(game_id)

                def on_failure(game_id: str, error: str) -> None:
                    _log.warning(
                        "remote config: startup recovery failed game_id=%s error=%s (will retry)",
                        game_id,
                        error,
                    )
                    rt.remote_downloading_game_ids.discard(game_id)

                return on_success, on_failure

            on_success, on_failure = make_callbacks(game_id, expected_version)
            started = download_game_async(
                game_id,
                expected_version,
                on_success=on_success,
                on_failure=on_failure,
            )
            if not started:
                _log.debug(
                    "remote config: startup recovery download already in progress game_id=%s",
                    game_id,
                )

        # Mark recovery as complete immediately since we've spawned all async downloads
        # The completion tracking is now handled by the async callbacks
        rt.startup_missing_ready_games_recovery_done = True
        _log.info("remote config: startup recovery async downloads initiated")

    @staticmethod
    def _apply_menu_ready_from_games(ctx: AppContext, games_cfg: list[dict[str, Any]]) -> None:
        from runtime.sync.game_ready import resolve_authoritative_ready_ids

        previous = ctx.remote_menu_ready_game_ids
        authoritative = resolve_authoritative_ready_ids(
            previous,
            games_cfg,
            games_key_present=True,
        )
        if authoritative is None:
            return
        if previous == authoritative:
            ctx.reload_game_menu = True
            return
        ctx.remote_menu_ready_game_ids = authoritative
        ctx.reload_game_menu = True

    def _run_startup_game_settlement(
        self,
        games_cfg: list[dict[str, Any]],
        cfg_ts: Optional[datetime],
    ) -> None:
        rt = self._runtime
        patch = games_settlement_patch_entries(games_cfg)
        if patch:
            try:
                self._deps.publish_partial_state({"games": patch})
            except Exception as e:
                _log.warning("remote config: startup settlement publish failed: %s", e)
        rt.awaiting_games_ready_confirmation = False
        rt.startup_settlement_completed = True
        rt.startup_games_ready_confirmed_at = cfg_ts
        rt.startup_filter_playing_until_newer_update = True
        _log.info(
            "remote config: startup game settlement complete; enabling remote game commands"
        )

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
        # App writes often bump state.device_updated_at only; row updated_at may lag.
        cfg_ts = resolve_snapshot_updated_at(config)

        if (
            deps.is_reset_in_progress()
            and is_remote_reset_confirmed(config)
            and is_remote_reset_confirmation_source(config.get("last_update_source"))
        ):
            deps.on_reset_confirmed()

        skip_game_commands = False
        skip_games_dedupe = should_skip_duplicate_remote_snapshot(rt, config)
        if skip_games_dedupe:
            _log.info(
                "remote config: skip duplicate bridge snapshot within %.1fs",
                _DUPLICATE_SNAPSHOT_WINDOW_SECONDS,
            )
        if rt.awaiting_games_ready_confirmation:
            games_cfg = normalize_games_list(config)
            self._apply_menu_ready_from_games(ctx, games_cfg)
            self._run_startup_game_settlement(games_cfg, cfg_ts)
            recovery_games = games_cfg_for_startup_recovery(
                games_cfg, after_settlement=True
            )
            self._recover_missing_ready_games_on_startup(recovery_games)
            skip_game_commands = True

        service = deps.get_machine_state_service()
        if service is None:
            return

        try:
            pages = config.get("pages")
            if isinstance(pages, list):
                source = str(config.get("last_update_source", "") or "").strip().lower()
                pages_updated_at = parse_iso_ts(config.get("pages_updated_at"))
                pages_fingerprint = json.dumps(
                    pages, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                should_reload_pages = False
                should_persist_pages = False

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
                    should_persist_pages = (
                        not rt.has_seen_remote_pages_snapshot
                        or pages_fingerprint != rt.last_applied_pages_fingerprint
                    )
                    rt.has_seen_remote_pages_snapshot = True
                    rt.last_applied_pages_fingerprint = pages_fingerprint
                else:
                    should_reload_pages = (
                        pages_fingerprint != rt.last_applied_non_bridge_pages_fingerprint
                    )
                    should_persist_pages = should_reload_pages
                    rt.last_applied_non_bridge_pages_fingerprint = pages_fingerprint

                if should_persist_pages:
                    service.set_pages(pages, reload_pages=False)

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
                deps.bluetooth_scan_controller.apply_explicit_remote_lists(bluetooth_cfg)
                if bool(bluetooth_cfg.get("is_scan")):
                    # Always reconcile against OS at scan start. Full config snapshots from
                    # Supabase often include `controllers: []` even when a pad is connected;
                    # treating that as "do not merge" leaves connected devices missing.
                    deps.bluetooth_scan_controller.start_scan_if_requested(
                        sync_connected_controllers=True,
                    )
                # Only treat controllers as authoritative when the key is present; an
                # omitted key must not behave like [] (would spuriously unpair devices).
                if "controllers" in bluetooth_cfg:
                    _cval = bluetooth_cfg.get("controllers")
                    controllers = _cval if isinstance(_cval, list) else []
                else:
                    controllers = None
                scan_results = (
                    bluetooth_cfg.get("scan_results")
                    if isinstance(bluetooth_cfg.get("scan_results"), list)
                    else []
                )
                ctrl_rows = controllers if controllers is not None else []
                # Trigger connects from either list when app marks a row connecting.
                for source_list, rows in (
                    ("controllers", ctrl_rows),
                    ("scan_results", scan_results),
                ):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        status = str(row.get("status") or "").strip().lower()
                        if status not in {"connecting", "connect"}:
                            continue
                        mac = str(row.get("mac") or row.get("address") or "").strip()
                        if mac:
                            deps.bluetooth_scan_controller.start_connect_if_requested(
                                mac, source_list
                            )

                # Detect controller removals and unpair removed devices.
                # Skip while scanning: snapshots may carry stale empty controllers.
                if controllers is not None and not bool(bluetooth_cfg.get("is_scan")):
                    current_controller_macs: set[str] = set()
                    for row in controllers:
                        if not isinstance(row, dict):
                            continue
                        mac = str(row.get("mac") or row.get("address") or "").strip().upper()
                        if mac:
                            current_controller_macs.add(mac)
                    removed_macs = rt.last_remote_controller_macs - current_controller_macs
                    for removed_mac in removed_macs:
                        try:
                            deps.disconnect_and_unpair_device(removed_mac)
                        except Exception as e:
                            _log.warning(
                                "Error removing remote bluetooth controller mac=%s: %s",
                                removed_mac,
                                e,
                            )
                    rt.last_remote_controller_macs = current_controller_macs
        except Exception as e:
            _log.warning("Error handling remote bluetooth config: %s", e)

        try:
            if "brightness" in config or "Brightness" in config:
                try:
                    key = "brightness" if "brightness" in config else "Brightness"
                    brightness_val = int(config.get(key))
                    current = None
                    try:
                        current = int((ctx.get_device_info() or {}).get("brightness"))
                    except Exception:
                        current = None
                    setting_source = str(config.get("last_update_source", "") or "").strip().lower()
                    if current != brightness_val and should_accept_remote_setting(
                        rt,
                        "brightness",
                        brightness_val,
                        cfg_ts,
                        source=setting_source,
                    ):
                        service.set_brightness(brightness_val)
                except Exception:
                    pass

            if "volume" in config:
                try:
                    volume_val = int(config.get("volume"))
                    current = None
                    try:
                        current = int((ctx.get_device_info() or {}).get("volume"))
                    except Exception:
                        current = None
                    setting_source = str(config.get("last_update_source", "") or "").strip().lower()
                    if current != volume_val and should_accept_remote_setting(
                        rt,
                        "volume",
                        volume_val,
                        cfg_ts,
                        source=setting_source,
                    ):
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

            if "games" not in config:
                games_cfg = []
            else:
                games_cfg = normalize_games_list(config)
                self._reconcile_local_game_folders(games_cfg)
                if games_cfg and (skip_game_commands or skip_games_dedupe):
                    self._reconcile_downloading_games(games_cfg)
                if not skip_game_commands:
                    self._apply_menu_ready_from_games(ctx, games_cfg)

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

            if skip_game_commands or skip_games_dedupe:
                pass
            else:
                self._recover_missing_ready_games_on_startup(games_cfg)

                source = str(config.get("last_update_source", "") or "").strip().lower()
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
                            record_remote_playing_accepted(rt, gid, cfg_ts)
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

                    if status == "playing" and not should_accept_remote_playing_command(
                        rt, game_id, cfg_ts, source=source
                    ):
                        _log.info(
                            "remote config: ignore stale playing game_id=%s cfg_ts=%s",
                            game_id,
                            cfg_ts,
                        )
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
