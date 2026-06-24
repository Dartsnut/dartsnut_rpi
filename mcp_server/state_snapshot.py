from __future__ import annotations

import json
import os
import time
from typing import Any

SNAPSHOT_PATH = "/tmp/dartsnut_ui_state.json"


def read_snapshot(path: str | None = None) -> dict[str, Any]:
    resolved_path = path or SNAPSHOT_PATH
    with open(resolved_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("UI snapshot is not an object")
    try:
        stat = os.stat(resolved_path)
        data["age_seconds"] = max(0.0, time.time() - stat.st_mtime)
    except OSError:
        pass
    return data
