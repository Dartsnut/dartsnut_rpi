"""Supabase sync engine: tokenization, reduction, outbox, and transport helpers."""

__all__ = ["SyncEngine", "SyncEvent"]


def __getattr__(name: str):
    if name == "SyncEngine":
        from runtime.sync.engine import SyncEngine

        return SyncEngine
    if name == "SyncEvent":
        from runtime.sync.events import SyncEvent

        return SyncEvent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
