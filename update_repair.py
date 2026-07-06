from __future__ import annotations

import os

PENDING_UPDATE_MARKERS = (
    "/boot/firmware/dartsnut_update_pending",
    "/boot/dartsnut_update_pending",
    "/var/lib/dartsnut/update_pending",
)
FORCEFSCK_PATH = "/forcefsck"


def _should_skip_missing_boot_parent(path: str) -> bool:
    parent = os.path.dirname(path)
    return path.startswith("/boot/") and not os.path.isdir(parent)


def mark_update_repair_pending() -> str | None:
    """Leave a reboot-persistent hint that update repair should run again."""
    first_written = None
    for marker in PENDING_UPDATE_MARKERS:
        try:
            if _should_skip_missing_boot_parent(marker):
                continue
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as file:
                file.write("pending\n")
            first_written = first_written or marker
        except OSError:
            continue
    return first_written


def clear_update_repair_pending() -> None:
    for marker in PENDING_UPDATE_MARKERS:
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def request_forcefsck() -> str | None:
    try:
        parent = os.path.dirname(FORCEFSCK_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(FORCEFSCK_PATH, "a", encoding="utf-8"):
            pass
        return FORCEFSCK_PATH
    except OSError:
        return None
