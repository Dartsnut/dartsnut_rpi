import time

from states.widget import WidgetState


class _Display:
    def __init__(self):
        self.last_image = None

    def update_frame_buffer(self, img):
        self.last_image = img


class _Assets:
    lock_widget_icon = None
    wifi_disconnect_icon = None
    internet_disconnect_icon = None


class _Ctx:
    def __init__(self, pages, page_index):
        self.pages = pages
        self.page_index = page_index
        self.page_freeze = False
        self.page_tick = time.time()
        self.next_page_prepared_index = -1
        self.last_page_index = page_index
        self.reload_conf = False
        self.assets = _Assets()
        self.display = _Display()
        self.wifi_connected = True
        self.internet_connected = True


def _page(uuid, enabled, duration="60"):
    return {
        "uuid": uuid,
        "enabled": enabled,
        "duration": duration,
        "widgets": [],
        "framebuffer": bytearray(128 * 160 * 3),
    }


def test_widget_state_leaves_default_page_when_real_page_reenabled():
    # Simulate being on default QR page ("0") after all user pages were disabled,
    # then one real page gets re-enabled.
    pages = [
        _page("user-page", True),
        _page("0", True),
    ]
    ctx = _Ctx(pages=pages, page_index=1)

    WidgetState().update(ctx)

    assert ctx.page_index == 0
    assert ctx.reload_conf is False


class _Process:
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None


def test_page_pause_and_resume_do_not_restart_widget_loading(monkeypatch):
    import states.widget as swidget

    first_widget = {
        "process": _Process(1),
        "shm": None,
        "widget": {"id": "first", "position": [0, 0, 127, 31]},
        "loading": False,
        "has_small_widget": None,
    }
    second_widget = {
        "process": _Process(2),
        "shm": None,
        "widget": {"id": "second", "position": [0, 0, 127, 31]},
        "loading": False,
        "has_small_widget": None,
    }
    pages = [
        {**_page("first-page", True), "widgets": [first_widget]},
        {**_page("second-page", True), "widgets": [second_widget]},
    ]
    ctx = _Ctx(pages=pages, page_index=1)
    ctx.last_page_index = 0
    signals = []
    monkeypatch.setattr(
        swidget,
        "signal_process_group",
        lambda pid, sig: signals.append((pid, sig)),
    )
    monkeypatch.setattr(swidget, "check_page_widget_updates", lambda *_args: None)

    WidgetState().update(ctx)

    assert first_widget["loading"] is False
    assert second_widget["loading"] is False
    assert {pid for pid, _sig in signals} == {1, 2}
