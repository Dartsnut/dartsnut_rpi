import subprocess
from types import SimpleNamespace

import pytest

from mcp_server import git_ops


def _cp(stdout: str = ""):
    return SimpleNamespace(stdout=stdout)


def test_perform_firmware_update_repairs_runtime_after_rollback(monkeypatch):
    git_calls = []
    sudo_calls = []
    repair_markers = []

    def fake_run_git(args, **kwargs):
        git_calls.append(args)
        if args == ["rev-parse", "HEAD"]:
            return _cp("oldsha\n")
        return _cp("")

    def fake_current_branch():
        return "master"

    def fake_run(cmd, **kwargs):
        sudo_calls.append(cmd)
        if cmd == ["sudo", "./update.sh"] and sudo_calls.count(cmd) == 1:
            raise subprocess.CalledProcessError(1, cmd)
        return _cp("")

    monkeypatch.setattr(git_ops, "_run_git", fake_run_git)
    monkeypatch.setattr(git_ops, "_current_branch", fake_current_branch)
    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    monkeypatch.setattr(
        git_ops,
        "mark_update_repair_pending",
        lambda: repair_markers.append("mark"),
    )
    monkeypatch.setattr(
        git_ops,
        "clear_update_repair_pending",
        lambda: repair_markers.append("clear"),
    )

    with pytest.raises(RuntimeError, match="rolled back"):
        git_ops.perform_firmware_update()

    assert repair_markers == ["mark", "clear"]
    assert git_calls == [
        ["rev-parse", "HEAD"],
        ["reset", "--hard"],
        ["fetch", "origin"],
        ["reset", "--hard", "origin/master"],
        ["reset", "--hard", "oldsha"],
    ]
    assert sudo_calls == [["sudo", "./update.sh"], ["sudo", "./update.sh"]]
