"""Entry point the firmware launches for a Scratch (sb3) game.

The firmware only ever runs `apps/<id>/.venv/bin/python main.py --shm <name>
--data-store <dir>`, so a Scratch game needs a main.py like any other game. This
shim is written by the firmware at install time and forwards to the shared Node
runner. It declares no Python dependencies of its own; the per-app virtualenv
exists only because the firmware's launch path expects one.

Nothing here is authored by the uploader, and nothing here reads project
metadata: the game's own identity lives in the backend sidecar.
"""

from __future__ import annotations

import os
import sys

RUNNER_FILENAME = "sb3_runner.js"


def _runtime_dir() -> str:
    return os.environ.get("DARTSNUT_SCRATCH_RUNTIME_DIR", "/var/lib/dartsnut/scratch-runtime")


def _node_bin() -> str:
    return os.environ.get("DARTSNUT_NODE_BIN", "node")


def main(argv: list[str]) -> int:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    runner = os.path.join(_runtime_dir(), RUNNER_FILENAME)
    if not os.path.isfile(runner):
        sys.stderr.write(f"scratch runner missing: {runner}\n")
        return 1

    node = _node_bin()
    # execvp, not execv: the service environment gives us a bare "node" and only
    # execvp searches PATH for it. execv would exec the literal string and die
    # with FileNotFoundError on every launch.
    #
    # exec replaces this process, so the Node runner inherits the pid the
    # firmware tracks, along with its process group and --shm/--data-store
    # arguments. A spawn-and-wait would leave a Python parent whose exit status
    # misrepresents the game.
    argv_out = [node, runner, "--project", app_dir, *argv]
    try:
        os.execvp(node, argv_out)
    except OSError as e:
        sys.stderr.write(f"failed to exec scratch runner via {node!r}: {e}\n")
        return 1
    # execvp only returns on failure.
    return 1



if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
