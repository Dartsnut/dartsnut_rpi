"""
Pluggable remote sync: Supabase bridge when available, otherwise no-op.

Presentation and config appliers depend on RemoteSyncPort, not on provider specifics.

When the bridge module is missing, `NoOpRemoteSync` is used: outbound publishes and
game-status requests are dropped. Local control still works via WebSocket RPC and the
device UI. A future optional implementation can mirror publishes over WebSocket when
`is_bridge_active()` is false without changing call sites.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol

try:
    import supabase_sync_bridge as _ssb

    _SUPABASE_AVAILABLE = True
except ImportError:
    _ssb = None  # type: ignore
    _SUPABASE_AVAILABLE = False


class RemoteSyncPort(Protocol):
    """Outbound device state + inbound config registration for remote sync."""

    def publish_partial_state(self, payload: Dict[str, Any]) -> None: ...

    def request_set_game_status(self, game_id: str, status: str) -> None: ...

    def request_set_all_games_ready(self) -> None: ...

    def request_device_reset_state(self) -> None: ...

    def is_connected(self) -> bool: ...

    def get_device_id(self) -> str: ...

    def set_connectivity_callback(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None: ...

    def start_sync_if_available(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        on_game_ready: Optional[Callable[[Any], None]] = None,
    ) -> None: ...

    def restart_sync(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        on_game_ready: Optional[Callable[[Any], None]] = None,
    ) -> None: ...

    def is_bridge_active(self) -> bool: ...

    def is_bridge_stale(self) -> bool: ...


class NoOpRemoteSync:
    """Used when the Supabase bridge module is missing or intentionally disabled."""

    def publish_partial_state(self, payload: Dict[str, Any]) -> None:
        return None

    def request_set_game_status(self, game_id: str, status: str) -> None:
        return None

    def request_set_all_games_ready(self) -> None:
        return None

    def request_device_reset_state(self) -> None:
        return None

    def is_connected(self) -> bool:
        return False

    def get_device_id(self) -> str:
        return ""

    def set_connectivity_callback(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None:
        return None

    def start_sync_if_available(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        on_game_ready: Optional[Callable[[Any], None]] = None,
    ) -> None:
        return None

    def restart_sync(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        on_game_ready: Optional[Callable[[Any], None]] = None,
    ) -> None:
        return None

    def is_bridge_active(self) -> bool:
        return False

    def is_bridge_stale(self) -> bool:
        return False


class SupabaseRemoteSync:
    """Delegates to supabase_sync_bridge (Unix-socket external bridge)."""

    def publish_partial_state(self, payload: Dict[str, Any]) -> None:
        _ssb.publish_device_state_update(payload)

    def request_set_game_status(self, game_id: str, status: str) -> None:
        _ssb.request_set_game_status(game_id, status)

    def request_set_all_games_ready(self) -> None:
        _ssb.request_set_all_games_ready()

    def request_device_reset_state(self) -> None:
        _ssb.request_device_reset_state()

    def is_connected(self) -> bool:
        return _ssb.is_supabase_connected()

    def get_device_id(self) -> str:
        return _ssb.get_supabase_device_id()

    def set_connectivity_callback(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None:
        _ssb.set_supabase_connectivity_callback(callback)

    def start_sync_if_available(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        on_game_ready: Optional[Callable[[Any], None]] = None,
    ) -> None:
        _ssb.start_supabase_sync_if_available(
            device_info, reload_config, on_config_updated, on_game_ready=on_game_ready
        )

    def restart_sync(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
        on_game_ready: Optional[Callable[[Any], None]] = None,
    ) -> None:
        _ssb.restart_supabase_sync(
            device_info, reload_config, on_config_updated, on_game_ready=on_game_ready
        )

    def is_bridge_active(self) -> bool:
        return _ssb.is_supabase_bridge_active()

    def is_bridge_stale(self) -> bool:
        return _ssb.is_supabase_bridge_stale()


_sync_impl: Optional[RemoteSyncPort] = None


def create_default_remote_sync() -> RemoteSyncPort:
    if _SUPABASE_AVAILABLE:
        return SupabaseRemoteSync()
    return NoOpRemoteSync()


def get_remote_sync() -> RemoteSyncPort:
    global _sync_impl
    if _sync_impl is None:
        _sync_impl = NoOpRemoteSync()
    return _sync_impl


def set_remote_sync(impl: RemoteSyncPort) -> None:
    global _sync_impl
    _sync_impl = impl
