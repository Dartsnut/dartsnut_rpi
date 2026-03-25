"""
Pluggable remote sync: Firestore bridge when available, otherwise no-op.

Presentation and config appliers depend on RemoteSyncPort, not on firestore_sync_bridge.

When the bridge module is missing, `NoOpRemoteSync` is used: outbound publishes and
game-status requests are dropped. Local control still works via WebSocket RPC and the
device UI. A future optional implementation can mirror publishes over WebSocket when
`is_bridge_active()` is false without changing call sites.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol

try:
    import firestore_sync_bridge as _fsb

    _FIRESTORE_AVAILABLE = True
except ImportError:
    _fsb = None  # type: ignore
    _FIRESTORE_AVAILABLE = False


class RemoteSyncPort(Protocol):
    """Outbound device state + inbound config registration (Firestore bridge today)."""

    def publish_partial_state(self, payload: Dict[str, Any]) -> None: ...

    def request_set_game_status(self, game_id: str, status: str) -> None: ...

    def request_set_all_games_ready(self) -> None: ...

    def request_device_reset_state(self) -> None: ...

    def is_connected(self) -> bool: ...

    def set_connectivity_callback(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None: ...

    def start_sync_if_available(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
    ) -> None: ...

    def restart_sync(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
    ) -> None: ...

    def is_bridge_active(self) -> bool: ...


class NoOpRemoteSync:
    """Used when the Firestore bridge module is missing or intentionally disabled."""

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

    def set_connectivity_callback(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None:
        return None

    def start_sync_if_available(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
    ) -> None:
        return None

    def restart_sync(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
    ) -> None:
        return None

    def is_bridge_active(self) -> bool:
        return False


class FirestoreRemoteSync:
    """Delegates to firestore_sync_bridge (Unix-socket Go bridge)."""

    def publish_partial_state(self, payload: Dict[str, Any]) -> None:
        _fsb.notify_device_state_update(payload)

    def request_set_game_status(self, game_id: str, status: str) -> None:
        _fsb.request_set_game_status(game_id, status)

    def request_set_all_games_ready(self) -> None:
        _fsb.request_set_all_games_ready()

    def request_device_reset_state(self) -> None:
        _fsb.request_device_reset_state()

    def is_connected(self) -> bool:
        return _fsb.is_firestore_connected()

    def set_connectivity_callback(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None:
        _fsb.set_firestore_connectivity_callback(callback)

    def start_sync_if_available(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
    ) -> None:
        _fsb.start_firestore_sync_if_available(
            device_info, reload_config, on_config_updated
        )

    def restart_sync(
        self,
        device_info: Dict[str, Any],
        reload_config: Callable[[], None],
        on_config_updated: Callable[[Dict[str, Any]], None],
    ) -> None:
        _fsb.restart_firestore_sync(device_info, reload_config, on_config_updated)

    def is_bridge_active(self) -> bool:
        return _fsb.is_firestore_bridge_active()


_sync_impl: Optional[RemoteSyncPort] = None


def create_default_remote_sync() -> RemoteSyncPort:
    if _FIRESTORE_AVAILABLE:
        return FirestoreRemoteSync()
    return NoOpRemoteSync()


def get_remote_sync() -> RemoteSyncPort:
    global _sync_impl
    if _sync_impl is None:
        _sync_impl = NoOpRemoteSync()
    return _sync_impl


def set_remote_sync(impl: RemoteSyncPort) -> None:
    global _sync_impl
    _sync_impl = impl
