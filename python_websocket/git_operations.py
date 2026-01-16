import subprocess
from python_websocket.error_handler import (
    ErrorCode,
    handle_exception,
    handle_command_error,
    create_error_response,
)


def get_version():
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd="/home/rpi/dartsnut_rpi",
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


def check_update():
    try:
        subprocess.run(
            ["git", "fetch", "origin"], cwd="/home/rpi/dartsnut_rpi", check=True
        )
        result = subprocess.run(
            ["git", "describe", "--tags", "origin/release", "--abbrev=0"],
            cwd="/home/rpi/dartsnut_rpi",
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        latest_tag = result.stdout.strip()
        return {"action": "check_update", "latest_version": latest_tag}
    except subprocess.CalledProcessError as e:
        return handle_command_error(
            "check_update", "git fetch/describe", e.returncode, e.stderr
        )
    except Exception as e:
        return handle_exception("check_update", e, "Failed to check for updates")


def perform_update():
    try:
        # Save current commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd="/home/rpi/dartsnut_rpi",
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        old_commit = result.stdout.strip()

        subprocess.run(
            ["git", "reset", "--hard"], cwd="/home/rpi/dartsnut_rpi", check=True
        )
        subprocess.run(
            ["git", "fetch", "origin"], cwd="/home/rpi/dartsnut_rpi", check=True
        )
        subprocess.run(
            ["git", "reset", "--hard", "origin/release"],
            cwd="/home/rpi/dartsnut_rpi",
            check=True,
        )

        # Check if setup.sh has changed
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", old_commit, "HEAD", "--", "setup.sh"],
            cwd="/home/rpi/dartsnut_rpi",
            stdout=subprocess.PIPE,
            text=True,
        )

        if "setup.sh" in diff_result.stdout:
            subprocess.run(
                ["sudo", "./setup.sh"], cwd="/home/rpi/dartsnut_rpi", check=True
            )
            return {"action": "perform_update", "message": "Update successful"}
        else:
            # Run update.sh to update dependencies and restart service
            subprocess.run(
                ["sudo", "./update.sh"], cwd="/home/rpi/dartsnut_rpi", check=True
            )
            return {"action": "perform_update", "message": "Update successful"}
    except subprocess.CalledProcessError as e:
        # Rollback to old commit
        try:
            subprocess.run(
                ["git", "reset", "--hard", old_commit],
                cwd="/home/rpi/dartsnut_rpi",
                check=True,
            )
            # Extract error message
            error_msg = "Update failed"
            if e.stderr:
                try:
                    error_text = (
                        e.stderr.decode("utf-8")
                        if isinstance(e.stderr, bytes)
                        else e.stderr
                    )
                    if error_text.strip():
                        error_line = error_text.strip().split("\n")[0][:100]
                        error_msg = f"Update failed: {error_line}"
                except:
                    pass
            return create_error_response(
                "perform_update",
                ErrorCode.GIT_UPDATE_FAILED,
                "Unable to update the system. The system has been restored to the previous version",
            )
        except subprocess.CalledProcessError as rollback_error:
            # Extract error message
            error_msg = "Update failed"
            if e.stderr:
                try:
                    error_text = (
                        e.stderr.decode("utf-8")
                        if isinstance(e.stderr, bytes)
                        else e.stderr
                    )
                    if error_text.strip():
                        error_line = error_text.strip().split("\n")[0][:100]
                        error_msg = f"Update failed: {error_line}"
                except:
                    pass
            rollback_msg = "Rollback failed"
            if rollback_error.stderr:
                try:
                    rollback_text = (
                        rollback_error.stderr.decode("utf-8")
                        if isinstance(rollback_error.stderr, bytes)
                        else rollback_error.stderr
                    )
                    if rollback_text.strip():
                        rollback_line = rollback_text.strip().split("\n")[0][:100]
                        rollback_msg = f"Rollback failed: {rollback_line}"
                except:
                    pass
            return create_error_response(
                "perform_update",
                ErrorCode.GIT_ROLLBACK_FAILED,
                "Unable to update the system. Unable to restore the previous version",
            )
    except Exception as e:
        return handle_exception("perform_update", e, "Update failed")
