"""Regression tests for sync resilience under bad networks."""

from datetime import datetime, timedelta, timezone

import pytest

from runtime.sync.game_ready import resolve_authoritative_ready_ids
from runtime.sync.outbox import SyncOutbox
from runtime.sync.reducer import SyncReducer
from runtime.sync.tokenizer import tokenize_snapshot


def test_resolve_authoritative_ready_holds_when_all_playing():
    previous = frozenset({"chess", "darts"})
    games_cfg = [
        {"id": "chess", "status": "playing"},
        {"id": "darts", "status": "downloading"},
    ]
    assert resolve_authoritative_ready_ids(previous, games_cfg, games_key_present=True) is None


def test_resolve_authoritative_ready_clears_on_explicit_empty():
    previous = frozenset({"chess"})
    assert resolve_authoritative_ready_ids(previous, [], games_key_present=True) == frozenset()


def test_reducer_does_not_clear_ready_on_playing_only_snapshot():
    reducer = SyncReducer()
    reducer.cache.game_ready_ids = frozenset({"g1"})
    reducer.cache.last_row_updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(
        tzinfo=None
    )
    config = {
        "updated_at": "2026-06-01T12:00:00Z",
        "last_update_source": "mobile_app",
        "games": [{"id": "g1", "status": "playing", "version": "1"}],
    }
    events = tokenize_snapshot(config, emit_full_snapshot=False)
    _, game_ready = reducer.reduce(events)
    assert game_ready is None
    assert reducer.cache.game_ready_ids == frozenset({"g1"})


def test_outbox_retries_when_send_returns_false():
    calls = []

    def send_fn(ref, patch, full, source):
        calls.append((ref, patch))
        return len(calls) >= 2

    outbox = SyncOutbox(send_fn, backoff_seconds=(0.01, 0.02))
    outbox.start()
    ref = outbox.enqueue({"brightness": 50})
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if outbox.pending_count() == 0:
            break
        time.sleep(0.05)
    outbox.stop()
    assert len(calls) >= 2
    assert calls[0][1] == {"brightness": 50}


def test_outbox_ack_removes_pending():
    sent = []

    def send_fn(ref, patch, full, source):
        sent.append(ref)
        return True

    outbox = SyncOutbox(send_fn, backoff_seconds=(0.01,))
    outbox.start()
    ref = outbox.enqueue({"volume": 10})
    outbox.on_ack(ref)
    assert outbox.pending_count() == 0
    outbox.stop()
