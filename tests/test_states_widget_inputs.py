from __future__ import annotations

import time as _time

import states.widget as swidget
from states.widget import WidgetState


class _Ctx:
    def __init__(self, pages, page_index):
        self.pages = pages
        self.page_index = page_index
        self.page_freeze = False
        self.page_tick = 0.0
        self.transitions = []

    def get_device_info(self):
        return {}

    def transition_to(self, state):
        self.transitions.append(type(state).__name__)


def test_widget_btn_a_toggles_freeze_and_updates_tick(monkeypatch):
    pages = []
    ctx = _Ctx(pages=pages, page_index=0)
    monkeypatch.setattr(swidget.time, "time", lambda: 123.0)

    WidgetState().handle_input(ctx, {"btn_a": True})
    assert ctx.page_freeze is True
    assert ctx.page_tick == 123.0


def test_widget_left_right_selects_enabled_non_qr_pages(monkeypatch):
    pages = [
        {"uuid": "0", "enabled": True, "widgets": []},
        {"uuid": "a", "enabled": True, "widgets": []},
        {"uuid": "b", "enabled": False, "widgets": []},
        {"uuid": "c", "enabled": True, "widgets": []},
    ]
    ctx = _Ctx(pages=pages, page_index=1)  # at "a"
    monkeypatch.setattr(swidget.time, "time", lambda: 10.0)

    WidgetState().handle_input(ctx, {"btn_left": True})
    assert ctx.page_index == 3  # "c"
    assert ctx.page_tick == 10.0

    monkeypatch.setattr(swidget.time, "time", lambda: 11.0)
    WidgetState().handle_input(ctx, {"btn_right": True})
    assert ctx.page_index == 1  # back to "a" skipping uuid "0"
    assert ctx.page_tick == 11.0


def test_widget_btn_home_pixelboard_toggles_freeze(monkeypatch):
    pages = [{"uuid": "0", "enabled": True, "widgets": []}]
    ctx = _Ctx(pages=pages, page_index=0)
    monkeypatch.setattr(swidget, "is_pixelboard_device", lambda: True)
    monkeypatch.setattr(swidget.time, "time", lambda: 42.0)

    WidgetState().handle_input(ctx, {"btn_home": True})
    assert ctx.page_freeze is True
    assert ctx.transitions == []
    assert ctx.page_tick == 42.0


def test_widget_btn_home_other_model_transitions_to_menu(monkeypatch):
    pages = [{"uuid": "0", "enabled": True, "widgets": []}]
    ctx = _Ctx(pages=pages, page_index=0)
    monkeypatch.setattr(swidget, "is_pixelboard_device", lambda: False)
    monkeypatch.setattr(swidget.time, "time", lambda: 1.0)

    WidgetState().handle_input(ctx, {"btn_home": True})
    assert ctx.transitions[-1] == "MenuState"


def test_widget_navigation_includes_page_with_unlaunched_widgets(monkeypatch):
    pages = [
        {"uuid": "0", "enabled": True, "widgets": []},
        {
            "uuid": "missing-widget-page",
            "enabled": True,
            "widgets": [
                {
                    "process": None,
                    "shm": None,
                    "widget": {"id": "digitalclock", "position": [0, 0, 127, 127], "fields": {}},
                    "loading": False,
                    "has_small_widget": None,
                }
            ],
        },
        {"uuid": "other-page", "enabled": True, "widgets": []},
    ]
    ctx = _Ctx(pages=pages, page_index=2)
    monkeypatch.setattr(swidget.time, "time", lambda: 77.0)

    WidgetState().handle_input(ctx, {"btn_left": True})
    assert ctx.page_index == 1
    assert ctx.page_tick == 77.0
