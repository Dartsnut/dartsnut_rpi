import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import python_websocket.git_operations as git_operations


def _cp(stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


def test_get_current_branch_uses_origin_head_when_detached(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("HEAD\n")
        if cmd == [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "--points-at",
            "HEAD",
            "refs/remotes/origin",
        ]:
            return _cp("origin/master\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    assert git_operations._get_current_branch() == "master"
    assert calls[:2] == [
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "--points-at",
            "HEAD",
            "refs/remotes/origin",
        ],
    ]


def test_get_current_branch_falls_back_to_single_remote_contains(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("HEAD\n")
        if cmd == [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "--points-at",
            "HEAD",
            "refs/remotes/origin",
        ]:
            return _cp("")
        if cmd == ["git", "branch", "-r", "--contains", "HEAD"]:
            return _cp("  origin/feature-x\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    assert git_operations._get_current_branch() == "feature-x"


def test_get_current_branch_errors_when_detached_is_ambiguous(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("HEAD\n")
        if cmd == [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "--points-at",
            "HEAD",
            "refs/remotes/origin",
        ]:
            return _cp("")
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


def test_check_update_non_release_avoids_tag_commands(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "fetch", "origin"]:
            return _cp("")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("feature/supabase\n")
        if cmd == ["git", "rev-parse", "--verify", "origin/feature/supabase"]:
            return _cp("")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        if cmd == ["git", "rev-parse", "origin/feature/supabase"]:
            return _cp("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n")
        if cmd[:2] == ["git", "describe"]:
            raise AssertionError("Tag command should not run on non-release branches")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.check_update()
    assert result["action"] == "check_update"
    assert result["current_version"] == "aaaaaaa"
    assert result["latest_version"] == "feature/supabase@bbbbbbb"
    assert result["needs_update"] is True


def test_check_update_release_uses_tag_commands(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "fetch", "origin"]:
            return _cp("")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("release\n")
        if cmd == ["git", "describe", "--tags", "origin/release", "--abbrev=0"]:
            return _cp("v1.2.3\n")
        if cmd == ["git", "describe", "--tags", "--abbrev=0"]:
            return _cp("v1.2.2\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.check_update()
    assert result["action"] == "check_update"
    assert result["current_version"] == "v1.2.2"
    assert result["latest_version"] == "v1.2.3"
    assert result["needs_update"] is True


def test_get_version_falls_back_to_v100_with_head_hash(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("release\n")
        if cmd == ["git", "describe", "--tags", "--abbrev=0"]:
            raise subprocess.CalledProcessError(128, cmd, stderr="no tag")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("0123456789abcdef0123456789abcdef01234567\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.get_version()
    assert result["action"] == "get_version"
    assert result["version"] == "v100.0.0123456"


def test_get_version_non_release_uses_head_hash_without_describe(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("master\n")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("89abcdef0123456789abcdef0123456789abcdef\n")
        if cmd[:2] == ["git", "describe"]:
            raise AssertionError("Tag command should not run on non-release branches")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.get_version()
    assert result["action"] == "get_version"
    assert result["version"] == "v100.0.89abcde"


def test_check_update_release_falls_back_current_version_when_no_tag(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd == ["git", "fetch", "origin"]:
            return _cp("")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _cp("release\n")
        if cmd == ["git", "describe", "--tags", "origin/release", "--abbrev=0"]:
            return _cp("v1.2.3\n")
        if cmd == ["git", "describe", "--tags", "--abbrev=0"]:
            raise subprocess.CalledProcessError(128, cmd, stderr="no tag")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("abcdef0123456789abcdef0123456789abcdef01\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.check_update()
    assert result["action"] == "check_update"
    assert result["current_version"] == "v100.0.abcdef0"
    assert result["latest_version"] == "v1.2.3"
    assert result["needs_update"] is True


def test_perform_update_defers_terminal_actions_and_calls_callback_before_restart(monkeypatch):
    calls = []
    callback_calls = []

    monkeypatch.setattr(git_operations, "_get_current_branch", lambda: "master")

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("oldsha\n")
        return _cp("")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.perform_update(
        before_terminal_action=lambda: callback_calls.append(len(calls))
    )

    assert result["action"] == "perform_update"
    assert callback_calls == [7]
    assert calls[3][0] == ["git", "rev-parse", "origin/master"]
    assert calls[4][0] == ["git", "reset", "--hard", "origin/master"]
    assert calls[5][0] == [
        "sudo",
        "env",
        "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1",
        "./update.sh",
    ]
    assert calls[5][1] is None
    assert calls[6][0] == [
        "env",
        "DARTSNUT_KERNEL_ROLLBACK_DEFER_REBOOT=1",
        "scripts/rollback_rpi_kernel_6_12.sh",
    ]
    assert calls[7][0] == ["sudo", "systemctl", "restart", "dartsnut_matrix.service"]
    assert calls[8][0] == ["sudo", "systemctl", "restart", "dartsnut_mcp.service"]
    assert calls[9][0] == ["sudo", "systemctl", "restart", "dartsnut_watchdog.service"]
    assert calls[10][0] == ["sudo", "systemctl", "restart", "dartsnut_python.service"]


def test_perform_update_calls_callback_right_before_reboot(monkeypatch):
    calls = []
    callback_calls = []

    monkeypatch.setattr(git_operations, "_get_current_branch", lambda: "master")

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("check")))
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("oldsha\n")
        if cmd == [
            "env",
            "DARTSNUT_KERNEL_ROLLBACK_DEFER_REBOOT=1",
            "scripts/rollback_rpi_kernel_6_12.sh",
        ]:
            return _cp("", returncode=git_operations.KERNEL_ROLLBACK_REBOOT_DEFERRED)
        return _cp("")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.perform_update(
        before_terminal_action=lambda: callback_calls.append(len(calls))
    )

    assert result["action"] == "perform_update"
    assert callback_calls == [7]
    assert calls[6][0] == [
        "env",
        "DARTSNUT_KERNEL_ROLLBACK_DEFER_REBOOT=1",
        "scripts/rollback_rpi_kernel_6_12.sh",
    ]
    assert calls[7][0] == ["sudo", "reboot"]
    assert not any(
        cmd[:3] == ["sudo", "systemctl", "restart"] for cmd, _check in calls
    )


def test_perform_update_without_callback_uses_direct_update_script(monkeypatch):
    calls = []

    monkeypatch.setattr(git_operations, "_get_current_branch", lambda: "master")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("oldsha\n")
        if cmd == ["git", "rev-parse", "origin/master"]:
            return _cp("newsha\n")
        return _cp("")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.perform_update()

    assert result["action"] == "perform_update"
    assert calls == [
        ["git", "rev-parse", "HEAD"],
        ["git", "reset", "--hard"],
        ["git", "fetch", "origin"],
        ["git", "rev-parse", "origin/master"],
        ["git", "reset", "--hard", "origin/master"],
        ["sudo", "./update.sh"],
    ]


def test_perform_update_skips_install_when_already_at_remote_head(monkeypatch):
    calls = []
    callback_calls = []

    monkeypatch.setattr(git_operations, "_get_current_branch", lambda: "master")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("same-sha\n")
        if cmd == ["git", "rev-parse", "origin/master"]:
            return _cp("same-sha\n")
        return _cp("")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.perform_update(
        before_terminal_action=lambda: callback_calls.append(len(calls))
    )

    assert result == {
        "action": "perform_update",
        "message": "Already up to date",
        "updated": False,
    }
    assert callback_calls == []
    assert calls == [
        ["git", "rev-parse", "HEAD"],
        ["git", "reset", "--hard"],
        ["git", "fetch", "origin"],
        ["git", "rev-parse", "origin/master"],
    ]


def test_perform_update_repairs_runtime_after_rollback(monkeypatch):
    calls = []
    repair_markers = []
    callback_calls = []

    monkeypatch.setattr(git_operations, "_get_current_branch", lambda: "master")
    monkeypatch.setattr(
        git_operations,
        "mark_update_repair_pending",
        lambda: repair_markers.append("mark"),
    )
    monkeypatch.setattr(
        git_operations,
        "clear_update_repair_pending",
        lambda: repair_markers.append("clear"),
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("oldsha\n")
        if (
            cmd
            == ["sudo", "env", "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1", "./update.sh"]
            and sum(c[0] == cmd for c in calls) == 1
        ):
            raise subprocess.CalledProcessError(1, cmd)
        return _cp("")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.perform_update(
        before_terminal_action=lambda: callback_calls.append(len(calls))
    )

    assert result["error_code"] == "6004"
    assert callback_calls == [9]
    assert repair_markers == ["mark", "clear"]
    assert [cmd for cmd, _env in calls] == [
        ["git", "rev-parse", "HEAD"],
        ["git", "reset", "--hard"],
        ["git", "fetch", "origin"],
        ["git", "rev-parse", "origin/master"],
        ["git", "reset", "--hard", "origin/master"],
        ["sudo", "env", "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1", "./update.sh"],
        ["git", "reset", "--hard", "oldsha"],
        ["sudo", "env", "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1", "./update.sh"],
        [
            "env",
            "DARTSNUT_KERNEL_ROLLBACK_DEFER_REBOOT=1",
            "scripts/rollback_rpi_kernel_6_12.sh",
        ],
        ["sudo", "systemctl", "restart", "dartsnut_matrix.service"],
        ["sudo", "systemctl", "restart", "dartsnut_mcp.service"],
        ["sudo", "systemctl", "restart", "dartsnut_watchdog.service"],
        ["sudo", "systemctl", "restart", "dartsnut_python.service"],
    ]


def test_perform_update_leaves_pending_repair_when_rollback_update_fails(monkeypatch):
    calls = []
    repair_markers = []
    callback_calls = []

    monkeypatch.setattr(git_operations, "_get_current_branch", lambda: "master")
    monkeypatch.setattr(
        git_operations,
        "mark_update_repair_pending",
        lambda: repair_markers.append("mark"),
    )
    monkeypatch.setattr(
        git_operations,
        "clear_update_repair_pending",
        lambda: repair_markers.append("clear"),
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        if cmd == ["git", "rev-parse", "HEAD"]:
            return _cp("oldsha\n")
        if cmd == [
            "sudo",
            "env",
            "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1",
            "./update.sh",
        ]:
            raise subprocess.CalledProcessError(1, cmd)
        return _cp("")

    monkeypatch.setattr(git_operations.subprocess, "run", fake_run)

    result = git_operations.perform_update(
        before_terminal_action=lambda: callback_calls.append(len(calls))
    )

    assert result["error_code"] == "6005"
    assert callback_calls == [8]
    assert repair_markers == ["mark"]
    assert [cmd for cmd, _env in calls] == [
        ["git", "rev-parse", "HEAD"],
        ["git", "reset", "--hard"],
        ["git", "fetch", "origin"],
        ["git", "rev-parse", "origin/master"],
        ["git", "reset", "--hard", "origin/master"],
        ["sudo", "env", "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1", "./update.sh"],
        ["git", "reset", "--hard", "oldsha"],
        ["sudo", "env", "DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1", "./update.sh"],
    ]
