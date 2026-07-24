import pytest
from PIL import Image

from runtime.snackbar_display import (
    CONTROLLER_DISCONNECTED_MESSAGE,
    FIRMWARE_UPDATE_FAILED_FALLBACK_CODE,
    FIRMWARE_UPDATE_FAILED_MESSAGE,
    MAIN_SURFACE_HEIGHT,
    SNACKBAR_ERROR_BACKGROUND,
    SNACKBAR_ERROR_FOREGROUND,
    SNACKBAR_HEIGHT,
    SnackbarDisplay,
    wrap_firmware_update_with_snackbar,
)


def test_controller_disconnected_message_is_available():
    assert CONTROLLER_DISCONNECTED_MESSAGE == "CONTROLLER DISCONNECTED"


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _Display:
    def __init__(self, results=None):
        self.frames = []
        self.results = list(results or [])

    def update_frame_buffer(self, frame):
        self.frames.append(frame.copy())
        return self.results.pop(0) if self.results else True


def test_snackbar_animates_and_expires_without_touching_pdm():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    base = Image.new("RGB", (128, 160), "red")
    pdm = Image.new("RGB", (128, 32), "blue")
    base.paste(pdm, (0, 128))
    display.update_frame_buffer(base)

    display.show_snackbar("TEST")
    clock.now = 0.1
    display.present()
    halfway = target.frames[-1]
    halfway_top = MAIN_SURFACE_HEIGHT - (SNACKBAR_HEIGHT // 2)
    assert halfway.getpixel((0, halfway_top - 1)) == (255, 0, 0)
    assert halfway.getpixel((0, halfway_top)) == (255, 255, 255)
    assert halfway.crop((0, 128, 128, 160)).tobytes() == pdm.tobytes()

    clock.now = 0.2
    display.present()
    assert target.frames[-1].getpixel(
        (0, MAIN_SURFACE_HEIGHT - SNACKBAR_HEIGHT)
    ) == (255, 255, 255)

    clock.now = 4.9
    display.present()
    assert target.frames[-1].getpixel((0, 126)) == (255, 255, 255)

    clock.now = 4.9
    display.present()
    assert target.frames[-1].getpixel(
        (0, MAIN_SURFACE_HEIGHT - SNACKBAR_HEIGHT)
    ) == (255, 0, 0)
    assert target.frames[-1].getpixel((0, 124)) == (255, 255, 255)

    clock.now = 5.0
    display.present()
    restored = target.frames[-1]
    assert restored.getpixel((0, 120)) == (255, 0, 0)
    assert restored.crop((0, 128, 128, 160)).tobytes() == pdm.tobytes()


def test_main_surface_updates_preserve_pdm_and_continue_behind_snackbar():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    full = Image.new("RGB", (128, 160), "black")
    full.paste(Image.new("RGB", (128, 32), "blue"), (0, 128))
    display.update_frame_buffer(full)

    display.show_snackbar("TEST")
    clock.now = 1.0
    display.update_frame_buffer(Image.new("RGB", (128, 128), "green"))
    visible = target.frames[-1]
    assert visible.getpixel((0, 100)) == (0, 128, 0)
    assert visible.getpixel((0, 120)) == (255, 255, 255)
    assert visible.getpixel((0, 140)) == (0, 0, 255)

    clock.now = 6.0
    display.present()
    restored = target.frames[-1]
    assert restored.getpixel((0, 120)) == (0, 128, 0)
    assert restored.getpixel((0, 140)) == (0, 0, 255)


def test_busy_frame_is_retried_by_present():
    clock = _Clock()
    target = _Display(results=[False, True])
    display = SnackbarDisplay(target, monotonic=clock)
    assert display.update_frame_buffer(Image.new("RGB", (128, 160), "red")) is False
    assert display.present() is True
    assert len(target.frames) == 2
    assert target.frames[0].tobytes() == target.frames[1].tobytes()


def test_new_snackbar_replaces_message_and_restarts_timeout():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    display.update_frame_buffer(Image.new("RGB", (128, 160), "black"))
    display.show_snackbar("FIRST")
    clock.now = 4.9
    display.show_snackbar("SECOND")
    clock.now = 5.2
    display.present()
    assert target.frames[-1].getpixel(
        (0, MAIN_SURFACE_HEIGHT - SNACKBAR_HEIGHT)
    ) == (255, 255, 255)
    clock.now = 9.8
    display.present()
    assert target.frames[-1].getpixel((0, 126)) == (255, 255, 255)
    clock.now = 9.9
    display.present()
    assert target.frames[-1].getpixel((0, 120)) == (0, 0, 0)


def test_firmware_wrapper_stays_visible_until_update_finishes():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    display.update_frame_buffer(Image.new("RGB", (128, 160), "red"))

    def update(*, before_terminal_action=None):
        clock.now = 30.0
        display.present()
        assert target.frames[-1].getpixel((0, 124)) == (255, 255, 255)
        return {"message": "ok"}

    wrapped = wrap_firmware_update_with_snackbar(display, update)
    assert wrapped(before_terminal_action=object()) == {"message": "ok"}

    clock.now = 30.1
    display.present()
    assert target.frames[-1].getpixel((0, 124)) == (255, 255, 255)
    clock.now = 30.2
    display.present()
    assert target.frames[-1].getpixel((0, 124)) == (255, 0, 0)


def test_firmware_wrapper_replaces_progress_with_coded_failure_snackbar():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    base = Image.new("RGB", (128, 160), "green")
    pdm = Image.new("RGB", (128, 32), "blue")
    base.paste(pdm, (0, 128))
    display.update_frame_buffer(base)

    failure = {
        "error": "Git rollback failed (60-05)",
        "error_code": "6005",
    }

    def update():
        clock.now = 30.0
        display.present()
        assert target.frames[-1].getpixel((0, 124)) == (255, 255, 255)
        return failure

    wrapped = wrap_firmware_update_with_snackbar(display, update)
    assert wrapped() is failure
    assert display._message == f"{FIRMWARE_UPDATE_FAILED_MESSAGE} 6005"

    clock.now = 30.2
    display.present()
    failed_frame = target.frames[-1]
    assert failed_frame.getpixel((0, 124)) == SNACKBAR_ERROR_BACKGROUND
    snackbar_region = failed_frame.crop(
        (0, MAIN_SURFACE_HEIGHT - SNACKBAR_HEIGHT, 128, MAIN_SURFACE_HEIGHT)
    )
    color_extrema = snackbar_region.getextrema()
    assert all(maximum >= 250 for _, maximum in color_extrema)
    assert failed_frame.crop((0, 128, 128, 160)).tobytes() == pdm.tobytes()

    clock.now = 35.0
    display.present()
    assert target.frames[-1].getpixel((0, 124)) == (0, 128, 0)


def test_firmware_wrapper_shows_fallback_failure_code_when_update_raises():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    display.update_frame_buffer(Image.new("RGB", (128, 160), "green"))

    def update():
        clock.now = 10.0
        display.present()
        assert target.frames[-1].getpixel((0, 124)) == (255, 255, 255)
        raise RuntimeError("failed")

    wrapped = wrap_firmware_update_with_snackbar(display, update)
    with pytest.raises(RuntimeError, match="failed"):
        wrapped()

    assert display._message == (
        f"{FIRMWARE_UPDATE_FAILED_MESSAGE} {FIRMWARE_UPDATE_FAILED_FALLBACK_CODE}"
    )
    clock.now = 10.2
    display.present()
    assert target.frames[-1].getpixel((0, 124)) == SNACKBAR_ERROR_BACKGROUND

    clock.now = 15.0
    display.present()
    assert target.frames[-1].getpixel((0, 124)) == (0, 128, 0)


def test_stale_firmware_completion_does_not_dismiss_newer_snackbar():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    display.update_frame_buffer(Image.new("RGB", (128, 160), "black"))

    def update():
        display.show_snackbar("NEWER")
        return {"message": "ok"}

    wrapped = wrap_firmware_update_with_snackbar(display, update)
    assert wrapped() == {"message": "ok"}
    clock.now = 0.2
    display.present()
    assert target.frames[-1].getpixel(
        (0, MAIN_SURFACE_HEIGHT - SNACKBAR_HEIGHT)
    ) == (255, 255, 255)


def test_busy_expiry_retries_the_frame_that_removed_snackbar():
    clock = _Clock()
    target = _Display()
    display = SnackbarDisplay(target, monotonic=clock)
    display.update_frame_buffer(Image.new("RGB", (128, 160), "red"))
    display.show_snackbar("TEST")
    clock.now = 1.0
    display.present()

    target.results = [False]
    clock.now = 5.0
    assert display.present() is False
    expired_frame = target.frames[-1]
    assert expired_frame.getpixel((0, 120)) == (255, 0, 0)

    display.update_frame_buffer(Image.new("RGB", (128, 160), "green"))
    retried_expired_frame = target.frames[-1]
    assert retried_expired_frame.getpixel((0, 120)) == (255, 0, 0)

    assert display.present() is True
    display.update_frame_buffer(Image.new("RGB", (128, 160), "green"))
    assert target.frames[-1].getpixel((0, 120)) == (0, 128, 0)
