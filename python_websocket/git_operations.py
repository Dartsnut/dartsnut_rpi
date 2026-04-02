import os
import subprocess
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_command_error,
    create_error_response,
)

# Repo root = parent of python_websocket/ so git matches this install, not a hardcoded path.
GIT_REPO_CWD = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


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
        raise ValueError("Detached HEAD; need a named branch for update checks")
    return branch


def get_version():
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=GIT_REPO_CWD,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        version_tag = result.stdout.strip()
        return {"action": "get_version", "version": version_tag}
    except subprocess.CalledProcessError as e:
        return handle_command_error(
            "get_version", "git describe", e.returncode, e.stderr
        )
    except Exception as e:
        return handle_exception("get_version", e, "Failed to get version")


def _short_sha(full_hex: str) -> str:
    return full_hex[:7] if len(full_hex) >= 7 else full_hex


def check_update():
    try:
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
            current = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                cwd=GIT_REPO_CWD,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            current_tag = current.stdout.strip()
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


def perform_update():
    old_commit = None

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
        subprocess.run(
            ["git", "fetch", "origin"], cwd=GIT_REPO_CWD, check=True
        )
        branch = _get_current_branch()
        reset_ref = "origin/release" if branch == "release" else f"origin/{branch}"
        subprocess.run(
            ["git", "reset", "--hard", reset_ref],
            cwd=GIT_REPO_CWD,
            check=True,
        )

        # Check if setup.sh has changed
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", old_commit, "HEAD", "--", "setup.sh"],
            cwd=GIT_REPO_CWD,
            stdout=subprocess.PIPE,
            text=True,
        )

        if "setup.sh" in diff_result.stdout:
            subprocess.run(
                ["sudo", "./setup.sh"], cwd=GIT_REPO_CWD, check=True
            )
            # Create flag file to indicate successful update
            flag_path = "/tmp/firmware_updated.flag"
            try:
                with open(flag_path, "w") as f:
                    f.write("")
            except Exception:
                pass  # Ignore errors creating flag file
            return {"action": "perform_update", "message": "Update successful"}
        else:
            # Run update.sh to update dependencies and restart service
            subprocess.run(
                ["sudo", "./update.sh"], cwd=GIT_REPO_CWD, check=True
            )
            # Create flag file to indicate successful update
            flag_path = "/tmp/firmware_updated.flag"
            try:
                with open(flag_path, "w") as f:
                    f.write("")
            except Exception:
                pass  # Ignore errors creating flag file
            return {"action": "perform_update", "message": "Update successful"}
    except subprocess.CalledProcessError as e:
        # Rollback to old commit
        try:
            subprocess.run(
                ["git", "reset", "--hard", old_commit],
                cwd=GIT_REPO_CWD,
                check=True,
            )
            return create_error_response(
                "perform_update",
                ErrorCode.GIT_UPDATE_FAILED,
                "Unable to update the system. The system has been restored to the previous version",
            )
        except subprocess.CalledProcessError as rollback_error:
            return create_error_response(
                "perform_update",
                ErrorCode.GIT_ROLLBACK_FAILED,
                "Unable to update the system. Unable to restore the previous version",
            )
    except Exception as e:
        return handle_exception("perform_update", e, "Update failed")
