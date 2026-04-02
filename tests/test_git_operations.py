import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import python_websocket.git_operations as git_operations


def _cp(stdout: str = ""):
    return SimpleNamespace(stdout=stdout)


def test_get_current_branch_uses_origin_head_when_detached(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("HEAD\n")
        if cmd == ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
            return _cp("origin/master\n")
        if cmd == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/master"]:
            return _cp("")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    assert git_operations._get_current_branch() == "master"
    assert calls[:2] == [
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
    ]


def test_get_current_branch_falls_back_to_single_remote_contains(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("HEAD\n")
        if cmd == ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
            raise subprocess.CalledProcessError(1, cmd)
        if cmd == ["git", "branch", "-r", "--contains", "HEAD"]:
            return _cp("  origin/feature-x\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    assert git_operations._get_current_branch() == "feature-x"


def test_get_current_branch_errors_when_detached_is_ambiguous(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("HEAD\n")
        if cmd == ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
            raise subprocess.CalledProcessError(1, cmd)
        if cmd == ["git", "branch", "-r", "--contains", "HEAD"]:
            return _cp("  origin/main\n  origin/release\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    try:
        git_operations._get_current_branch()
    except ValueError as exc:
        assert "Detached HEAD" in str(exc)
    else:
        raise AssertionError("Expected ValueError for ambiguous detached HEAD")
