from __future__ import annotations

from typing import Callable, Protocol


class WaitableEvent(Protocol):
    def clear(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


def request_confirm_and_forget_wifi(
    *,
    request_device_reset_state: Callable[[], None],
    confirm_event: WaitableEvent,
    confirm_timeout_seconds: float,
    forget_wifi: Callable[[], None],
    log: Callable[[str], None] = print,
) -> bool:
    """Run reset confirmation wait and always proceed with local Wi-Fi removal."""
    confirm_event.clear()
    log("Reset: requesting remote reset confirmation")
    request_device_reset_state()
    confirmed = confirm_event.wait(confirm_timeout_seconds)
    if confirmed:
        log("Reset: remote confirmation received")
    else:
        log(
            f"Reset: remote confirmation timeout after {confirm_timeout_seconds:.0f}s; continuing with local reset"
        )
    log("Reset: forgetting Wi-Fi profiles")
    forget_wifi()
    log("Reset: Wi-Fi forget completed")
    return bool(confirmed)
