"""Shared runtime state for firmware upgrade coordination."""

from __future__ import annotations

import os
import tempfile


UPGRADE_STATE_PATH = "/run/dartsnut/upgrade_in_progress"


def set_upgrade_in_progress(in_progress: bool) -> None:
    """Atomically publish whether firmware update or rollback work is active."""
    directory = os.path.dirname(UPGRADE_STATE_PATH)
    temporary_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=".upgrade_in_progress.", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write("1\n" if in_progress else "0\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, UPGRADE_STATE_PATH)
    except Exception:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        if not in_progress:
            try:
                os.unlink(UPGRADE_STATE_PATH)
            except FileNotFoundError:
                return
            except OSError as unlink_error:
                raise RuntimeError(
                    "Unable to clear firmware upgrade state"
                ) from unlink_error
            return
        raise
