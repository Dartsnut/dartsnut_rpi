"""Orchestrates inbound tokenization/reduction and outbound outbox publishing."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from domain.app_context import AppContext
from runtime.remote_device_config import is_valid_games_list
from runtime.api_token_store import preserve_remote_user_token
from runtime.sync.event_applier import apply_events
from runtime.sync.outbox import KIND_PATCH, KIND_RESET, SyncOutbox
from runtime.sync.reducer import ReducedGameReady, SyncReducer
from runtime.sync.tokenizer import tokenize_snapshot

_log = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        *,
        on_apply_config: Callable[[Dict[str, Any]], None],
        merge_on_first_connect: Callable[[Dict[str, Any]], Dict[str, Any]],
        normalize_config: Callable[[Dict[str, Any]], Dict[str, Any]],
        remember_remote_game_ids: Callable[[Dict[str, Any]], None],
    ) -> None:
        self._on_apply_config = on_apply_config
        self._merge_on_first_connect = merge_on_first_connect
        self._normalize_config = normalize_config
        self._remember_remote_game_ids = remember_remote_game_ids
        self.reducer = SyncReducer()
        self.outbox: Optional[SyncOutbox] = None

    def attach_outbox(self, outbox: SyncOutbox) -> None:
        self.outbox = outbox

    def ingest_remote_row(
        self,
        payload: Dict[str, Any],
        *,
        is_first_after_connect: bool = False,
    ) -> bool:
        if not isinstance(payload, dict):
            return False

        if "games" in payload and not is_valid_games_list(payload.get("games")):
            _log.warning("sync engine: ignoring malformed games payload")
            return False

        preserve_remote_user_token(payload.get("user"))
        cfg = self._normalize_config(dict(payload))
        if is_first_after_connect:
            try:
                cfg = self._merge_on_first_connect(payload)
                cfg = self._normalize_config(cfg)
            except Exception:
                cfg = self._normalize_config(dict(payload))

        self._remember_remote_game_ids(cfg)
        events = tokenize_snapshot(
            cfg,
            is_first_after_connect=is_first_after_connect,
            emit_full_snapshot=True,
        )
        accepted, game_ready = self.reducer.reduce(events)
        return self._dispatch(accepted, game_ready)

    def apply_game_ready_to_ctx(
        self, ctx: AppContext, reduced: ReducedGameReady
    ) -> None:
        if not reduced.should_reload_menu and ctx.remote_menu_ready_game_ids is not None:
            if ctx.remote_menu_ready_game_ids == reduced.ready_ids:
                return
        ctx.remote_menu_ready_game_ids = reduced.ready_ids
        ctx.reload_game_menu = True

    def publish_partial(
        self,
        patch: Dict[str, Any],
        *,
        source: Optional[str] = None,
    ) -> str:
        """Queue a partial state patch; returns its outbox ref.

        The ref is "" when the patch was dropped (no outbox attached, empty
        patch) or absorbed into an earlier pending entry by scalar coalescing, so
        it is falsy exactly where the old boolean return was False or where there
        is no distinct write left to observe.
        """
        if not isinstance(patch, dict) or not patch:
            return ""
        if self.outbox is not None:
            return self.outbox.enqueue(patch, source=source, kind=KIND_PATCH)
        return ""

    def publish_full(
        self,
        patch: Dict[str, Any],
        *,
        source: Optional[str] = None,
    ) -> str:
        """Queue a full-state patch; returns its outbox ref ("" when dropped)."""
        if not isinstance(patch, dict) or not patch:
            return ""
        if self.outbox is not None:
            return self.outbox.enqueue(patch, full=True, source=source, kind=KIND_PATCH)
        return ""

    def publish_reset(self) -> str:
        """Queue a device reset; returns its outbox ref ("" when dropped).

        A reset is not a state patch: the bridge derives everything it sends (the
        device id) from its own configuration, so no caller-supplied payload
        reaches the wire and the firmware field sanitizer never sees this call.
        """
        if self.outbox is None:
            return ""
        return self.outbox.enqueue({}, source=None, kind=KIND_RESET)

    def ref_status(self, ref: str) -> Optional[str]:
        """Transport outcome of a queued ref, or None when nothing can answer.

        OUTCOME_PENDING until the transport reports anything, then OUTCOME_ACKED
        or OUTCOME_ERROR. "" and a ref this engine never queued both read as
        None: there is no outbox attached, or no such write, so no caller can
        watch it.
        """
        if self.outbox is None:
            return None
        return self.outbox.ref_status(ref)

    def _dispatch(
        self,
        accepted: list,
        game_ready: Optional[ReducedGameReady],
    ) -> bool:
        applied = False
        if accepted:
            try:
                apply_events(accepted, apply_config=self._on_apply_config)
                applied = True
            except Exception as e:
                _log.warning("sync engine: apply events failed: %s", e)
        self._pending_game_ready = game_ready
        return applied or game_ready is not None

    def consume_pending_game_ready(self) -> Optional[ReducedGameReady]:
        gr = getattr(self, "_pending_game_ready", None)
        self._pending_game_ready = None
        return gr
