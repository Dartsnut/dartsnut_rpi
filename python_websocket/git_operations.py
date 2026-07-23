import os
import subprocess
import sys
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_command_error,
    create_error_response,
)
from update_repair import clear_update_repair_pending, mark_update_repair_pending
from update_sources import (
    DIRECT_PYPI_INDEX,
    prepare_git_source,
    prepare_update_sources,
)

# Repo root = parent of python_websocket/ so git matches this install, not a hardcoded path.
GIT_REPO_CWD = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
KERNEL_ROLLBACK_SCRIPT = os.path.join("scripts", "rollback_rpi_kernel_6_12.sh")
KERNEL_ROLLBACK_REBOOT_DEFERRED = 77


def _run_update_script(*, defer_terminal_actions=False, uv_default_index=None):
    uv_default_index = uv_default_index or DIRECT_PYPI_INDEX
    cmd = ["sudo", "env", f"UV_DEFAULT_INDEX={uv_default_index}"]
    if defer_terminal_actions:
        cmd.append("DARTSNUT_UPDATE_DEFER_TERMINAL_ACTIONS=1")
    cmd.append("./update.sh")
    return subprocess.run(cmd, cwd=GIT_REPO_CWD, check=True)


def _prepare_git_and_uv_sources():
    try:
        return prepare_update_sources(GIT_REPO_CWD)
    except Exception as exc:
        print(
            f"Update sources: selection failed; using direct sources: {exc}",
            file=sys.stderr,
        )
        return DIRECT_PYPI_INDEX


def _prepare_git_source_only():
    try:
        prepare_git_source(GIT_REPO_CWD)
    except Exception as exc:
        print(
            f"Update sources: Git selection failed; using direct route: {exc}",
            file=sys.stderr,
        )


def _run_terminal_update_actions(before_terminal_action=None):
    if before_terminal_action is None:
        before_terminal_action = lambda: None

    rollback_result = subprocess.run(
        [
            "env",
            "DARTSNUT_KERNEL_ROLLBACK_DEFER_REBOOT=1",
            KERNEL_ROLLBACK_SCRIPT,
        ],
        cwd=GIT_REPO_CWD,
        check=False,
    )
    if rollback_result.returncode == KERNEL_ROLLBACK_REBOOT_DEFERRED:
        before_terminal_action()
        subprocess.run(["sudo", "reboot"], cwd=GIT_REPO_CWD, check=True)
        return
    if rollback_result.returncode != 0:
        raise subprocess.CalledProcessError(
            rollback_result.returncode,
            [KERNEL_ROLLBACK_SCRIPT],
        )

    before_terminal_action()
    for service in (
        "dartsnut_matrix.service",
        "dartsnut_mcp.service",
        "dartsnut_watchdog.service",
        "dartsnut_python.service",
    ):
        subprocess.run(
            ["sudo", "systemctl", "restart", service],
            cwd=GIT_REPO_CWD,
            check=True,
        )


def _call_once(callback):
    called = False

    def _wrapped():
        nonlocal called
        if called:
            return
        called = True
        if callback is not None:
            callback()

    return _wrapped


def _get_current_branch():
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=GIT_REPO_CWD,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    branch = result.stdout.strip()
    if branch == "HEAD":
        # Detached HEAD recovery:
        # 1) If exactly one origin/* ref points at HEAD, use it.
        # 2) Else if exactly one origin/* ref contains HEAD, use it.
        # 3) Else fail as ambiguous instead of guessing.
        points_at = subprocess.run(
            [
                "git",
                "for-each-ref",
                "--format=%(refname:short)",
                "--points-at",
                "HEAD",
                "refs/remotes/origin",
            ],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        point_candidates = []
        for line in points_at.stdout.splitlines():
            remote = line.strip()
            if not remote.startswith("origin/") or remote == "origin/HEAD":
                continue
            point_candidates.append(remote.split("/", 1)[1])
        unique_points = sorted(set(point_candidates))
        if len(unique_points) == 1:
            return unique_points[0]

        contains = subprocess.run(
            ["git", "branch", "-r", "--contains", "HEAD"],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        candidates = []
        for line in contains.stdout.splitlines():
            remote = line.strip().lstrip("*").strip()
            if not remote.startswith("origin/"):
                continue
            if "->" in remote:
                continue
            candidates.append(remote.split("/", 1)[1])
        unique = sorted(set(candidates))
        if len(unique) == 1:
            return unique[0]
        raise ValueError(
            "Detached HEAD; unable to determine a unique branch for update checks"
        )
    return branch


def get_version():
    try:
        branch = _get_current_branch()
        if branch != "release":
            version_tag = _fallback_version_from_head()
        else:
            try:
                result = subprocess.run(
                    ["git", "describe", "--tags", "--abbrev=0"],
                    cwd=GIT_REPO_CWD,
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                )
                version_tag = result.stdout.strip()
            except subprocess.CalledProcessError:
                version_tag = _fallback_version_from_head()
        return {"action": "get_version", "version": version_tag}
    except Exception as e:
        return handle_exception("get_version", e, "Failed to get version")


def _short_sha(full_hex: str) -> str:
    return full_hex[:7] if len(full_hex) >= 7 else full_hex


def _fallback_version_from_head() -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=GIT_REPO_CWD,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return f"v100.0.{_short_sha(head.stdout.strip())}"


def check_update():
    try:
        _prepare_git_source_only()
        subprocess.run(
            ["git", "fetch", "origin"], cwd=GIT_REPO_CWD, check=True
        )
        branch = _get_current_branch()
        if branch == "release":
            latest = subprocess.run(
                ["git", "describe", "--tags", "origin/release", "--abbrev=0"],
                cwd=GIT_REPO_CWD,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            latest_tag = latest.stdout.strip()
            try:
                current = subprocess.run(
                    ["git", "describe", "--tags", "--abbrev=0"],
                    cwd=GIT_REPO_CWD,
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                )
                current_tag = current.stdout.strip()
            except subprocess.CalledProcessError:
                current_tag = _fallback_version_from_head()
            return {
                "action": "check_update",
                "latest_version": latest_tag,
                "current_version": current_tag,
                "needs_update": current_tag != latest_tag,
            }
        remote_ref = f"origin/{branch}"
        subprocess.run(
            ["git", "rev-parse", "--verify", remote_ref],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        local_full = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        remote_full = subprocess.run(
            ["git", "rev-parse", remote_ref],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        local_sha = local_full.stdout.strip()
        remote_sha = remote_full.stdout.strip()
        return {
            "action": "check_update",
            "latest_version": f"{branch}@{_short_sha(remote_sha)}",
            "current_version": _short_sha(local_sha),
            "needs_update": local_sha != remote_sha,
        }
    except subprocess.CalledProcessError as e:
        return handle_command_error(
            "check_update", "git fetch/describe", e.returncode, e.stderr
        )
    except Exception as e:
        return handle_exception("check_update", e, "Failed to check for updates")


def perform_update(before_terminal_action=None):
    old_commit = None
    uv_default_index = DIRECT_PYPI_INDEX
    defer_terminal_actions = before_terminal_action is not None
    before_terminal = _call_once(before_terminal_action)

    try:
        # Save current commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        old_commit = result.stdout.strip()

        subprocess.run(
            ["git", "reset", "--hard"], cwd=GIT_REPO_CWD, check=True
        )
        uv_default_index = _prepare_git_and_uv_sources()
        subprocess.run(
            ["git", "fetch", "origin"], cwd=GIT_REPO_CWD, check=True
        )
        branch = _get_current_branch()
        reset_ref = "origin/release" if branch == "release" else f"origin/{branch}"
        remote_result = subprocess.run(
            ["git", "rev-parse", reset_ref],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        if old_commit == remote_result.stdout.strip():
            return {
                "action": "perform_update",
                "message": "Already up to date",
                "updated": False,
            }
        subprocess.run(
            ["git", "reset", "--hard", reset_ref],
            cwd=GIT_REPO_CWD,
            check=True,
        )

        # Always run update.sh for git-based updates.
        # setup.sh is reserved for first-time machine provisioning.
        _run_update_script(
            defer_terminal_actions=defer_terminal_actions,
            uv_default_index=uv_default_index,
        )
        if defer_terminal_actions:
            _run_terminal_update_actions(before_terminal)

        # Create flag file to indicate successful update
        flag_path = "/tmp/firmware_updated.flag"
        try:
            with open(flag_path, "w") as f:
                f.write("")
        except Exception:
            pass  # Ignore errors creating flag file
        return {"action": "perform_update", "message": "Update successful"}
    except subprocess.CalledProcessError as e:
        mark_update_repair_pending()
        # Rollback to old commit
        try:
            subprocess.run(
                ["git", "reset", "--hard", old_commit],
                cwd=GIT_REPO_CWD,
                check=True,
            )
            _run_update_script(
                defer_terminal_actions=defer_terminal_actions,
                uv_default_index=uv_default_index,
            )
            clear_update_repair_pending()
            if defer_terminal_actions:
                _run_terminal_update_actions(before_terminal)
            return create_error_response(
                "perform_update",
                ErrorCode.GIT_UPDATE_FAILED,
                "Unable to update the system. The system has been restored to the previous version",
            )
        except subprocess.CalledProcessError as rollback_error:
            before_terminal()
            return create_error_response(
                "perform_update",
                ErrorCode.GIT_ROLLBACK_FAILED,
                "Unable to update the system. Unable to restore the previous version",
            )
    except Exception as e:
        return handle_exception("perform_update", e, "Update failed")
