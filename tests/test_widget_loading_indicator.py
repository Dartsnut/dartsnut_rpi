from __future__ import annotations

from PIL import Image

from runtime.display_loop import update_widget_page_framebuffer


class _Shm:
    def __init__(self, width: int, height: int):
        self.buf = bytearray(1 + width * height * 3)
        self.buf[0] = 1


class _Assets:
    def __init__(self, color: str = "yellow"):
        self.loading_small = Image.new("RGB", (128, 32), color)
        self.loading_big = Image.new("RGB", (128, 128), color)

    def get_current_loading_frame(self):
        return self.loading_small

    def get_current_loading_frame_big(self):
        return self.loading_big


def _widget(y: int):
    return {
        "process": object(),
        "shm": _Shm(128, 32),
        "widget": {"id": f"widget-{y}", "position": [0, y, 127, y + 31]},
        "loading": True,
        "has_small_widget": None,
    }


def test_widgets_clear_loading_independently_and_accept_black_first_frame():
    widgets = [_widget(0), _widget(32), _widget(64)]
    page = {
        "framebuffer": bytearray(128 * 160 * 3),
        "widgets": widgets,
    }
    assets = _Assets()

    update_widget_page_framebuffer(page, assets)
    initial = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
    assert initial.getpixel((0, 0)) == (255, 255, 0)
    assert initial.getpixel((0, 32)) == (255, 255, 0)
    assert initial.getpixel((0, 64)) == (255, 255, 0)

    widgets[0]["shm"].buf[1:] = Image.new("RGB", (128, 32), "black").tobytes()
    widgets[0]["shm"].buf[0] = 0
    update_widget_page_framebuffer(page, assets)

    one_ready = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
    assert widgets[0]["loading"] is False
    assert widgets[1]["loading"] is True
    assert widgets[2]["loading"] is True
    assert one_ready.getpixel((0, 0)) == (0, 0, 0)
    assert one_ready.getpixel((0, 32)) == (255, 255, 0)
    assert one_ready.getpixel((0, 64)) == (255, 255, 0)

    widgets[1]["shm"].buf[1:] = Image.new("RGB", (128, 32), "green").tobytes()
    widgets[1]["shm"].buf[0] = 0
    update_widget_page_framebuffer(page, assets)

    two_ready = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
    assert widgets[0]["loading"] is False
    assert widgets[1]["loading"] is False
    assert widgets[2]["loading"] is True
    assert two_ready.getpixel((0, 0)) == (0, 0, 0)
    assert two_ready.getpixel((0, 32)) == (0, 128, 0)
    assert two_ready.getpixel((0, 64)) == (255, 255, 0)

    widgets[2]["shm"].buf[1:] = Image.new("RGB", (128, 32), "blue").tobytes()
    widgets[2]["shm"].buf[0] = 0
    update_widget_page_framebuffer(page, assets)

    assert [widget["loading"] for widget in widgets] == [False, False, False]


def test_ready_widget_keeps_cached_frame_while_other_widget_loading_animates():
    ready = _widget(0)
    loading = _widget(32)
    page = {
        "framebuffer": bytearray(128 * 160 * 3),
        "widgets": [ready, loading],
    }

    ready["shm"].buf[1:] = Image.new("RGB", (128, 32), "red").tobytes()
    ready["shm"].buf[0] = 0
    update_widget_page_framebuffer(page, _Assets("yellow"))
    update_widget_page_framebuffer(page, _Assets("blue"))

    result = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
    assert result.getpixel((0, 0)) == (255, 0, 0)
    assert result.getpixel((0, 32)) == (0, 0, 255)


class _ClosedShm:
    @property
    def buf(self):
        raise ValueError("operation forbidden on released memoryview object")


def test_closed_widget_shared_memory_keeps_loading_without_breaking_page():
    widget = _widget(0)
    widget["shm"] = _ClosedShm()
    page = {
        "framebuffer": bytearray(128 * 160 * 3),
        "widgets": [widget],
    }

    update_widget_page_framebuffer(page, _Assets())

    result = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
    assert widget["loading"] is True
    assert result.getpixel((0, 0)) == (255, 255, 0)


class _ExitedProcess:
    def poll(self):
        return 1


def test_exited_widget_process_replaces_cached_frame_with_loading_indicator():
    widget = _widget(0)
    widget["process"] = _ExitedProcess()
    widget["loading"] = False
    cached = Image.new("RGB", (128, 160), "red")
    page = {
        "framebuffer": bytearray(cached.tobytes()),
        "widgets": [widget],
    }

    update_widget_page_framebuffer(page, _Assets())

    result = Image.frombytes("RGB", (128, 160), bytes(page["framebuffer"]))
    assert result.getpixel((0, 0)) == (255, 255, 0)
