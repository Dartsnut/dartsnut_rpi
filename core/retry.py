"""Small retry helper with exponential backoff for transient operations."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)

# Four attempts total (~22s of waiting at most), styled after
# runtime/sync/outbox.py's backoff schedule.
DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0, 15.0)


def retry_with_backoff(
    fn: Callable[[], Any],
    *,
    succeeded: Callable[[Any], bool] = bool,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    label: str = "operation",
    sleep: Callable[[float], None] | None = None,
    reraise: bool = False,
) -> Any:
    """
    Call ``fn`` until it succeeds or attempts are exhausted.

    A failed attempt is one where ``fn`` raises or where ``succeeded(result)``
    is falsy. Between attempts we sleep ``backoff[i]`` seconds; the total number
    of attempts is ``len(backoff) + 1``.

    Returns the last result. If the final attempt raised and ``reraise`` is set,
    the exception is re-raised; otherwise the last result (or None) is returned.
    """
    if sleep is None:
        sleep = time.sleep
    attempts = len(backoff) + 1
    last_result: Any = None
    last_exc: BaseException | None = None

    for i in range(attempts):
        last_exc = None
        try:
            last_result = fn()
            if succeeded(last_result):
                if i:
                    _log.info("retry: %s succeeded on attempt %d/%d", label, i + 1, attempts)
                return last_result
            reason = "result not successful"
        except Exception as e:  # noqa: BLE001 - retry treats any error as transient
            last_exc = e
            last_result = None
            reason = f"raised {type(e).__name__}: {e}"

        if i < attempts - 1:
            delay = backoff[i]
            _log.warning(
                "retry: %s failed (attempt %d/%d, %s); retrying in %.1fs",
                label,
                i + 1,
                attempts,
                reason,
                delay,
            )
            sleep(delay)
        else:
            _log.warning(
                "retry: %s failed (attempt %d/%d, %s); giving up",
                label,
                i + 1,
                attempts,
                reason,
            )

    if reraise and last_exc is not None:
        raise last_exc
    return last_result
