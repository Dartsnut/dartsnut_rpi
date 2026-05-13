"""Deferred widget restarts after background download when leaving widget mode."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import widget_lifecycle as wl


@pytest.fixture(autouse=True)
def restore_widgets_updated():
    prev = set(wl.widgets_updated)
    yield
    wl.widgets_updated.clear()
    wl.widgets_updated.update(prev)


def test_flush_deferred_kills_matching_widgets_and_clears_set(monkeypatch):
    killed: list[tuple[str, str]] = []

    def track_kill(widget_entry, widget_id, reason=""):
        killed.append((widget_id, reason))

    monkeypatch.setattr(wl, "_kill_widget_process", track_kill)

    wl.widgets_updated.add("factory_tool")

    ctx = MagicMock()
    ctx.pages = [
        {
            "uuid": "p1",
            "widgets": [
                {
                    "process": MagicMock(),
                    "widget": {
                        "id": "factory_tool",
                        "position": [0, 0, 10, 10],
                        "fields": {},
                    },
                }
            ],
        }
    ]

    wl.flush_deferred_widget_processes_on_leave_widget_mode(ctx)

    assert killed == [
        ("factory_tool", "leaving widget mode, deferred update"),
    ]
    assert "factory_tool" not in wl.widgets_updated


def test_flush_deferred_noop_when_set_empty(monkeypatch):
    called = []

    monkeypatch.setattr(
        wl,
        "_kill_widget_process",
        lambda *a, **k: called.append(True),
    )

    wl.widgets_updated.clear()
    ctx = MagicMock()
    ctx.pages = [{"uuid": "p1", "widgets": []}]

    wl.flush_deferred_widget_processes_on_leave_widget_mode(ctx)

    assert called == []
