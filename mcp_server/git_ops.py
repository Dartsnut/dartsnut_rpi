from __future__ import annotations

import os
import subprocess
from typing import Any

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _run_git(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )


def _short_sha(full_hex: str) -> str:
    return full_hex[:7] if len(full_hex) >= 7 else full_hex


def _current_branch() -> str:
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "HEAD":
        return branch

    points_at = _run_git(
        [
            "for-each-ref",
            "--format=%(refname:short)",
            "--points-at",
            "HEAD",
            "refs/remotes/origin",
        ]
    )
    point_candidates = []
    for line in points_at.stdout.splitlines():
        remote = line.strip()
        if remote.startswith("origin/") and remote != "origin/HEAD":
            point_candidates.append(remote.split("/", 1)[1])
    unique_points = sorted(set(point_candidates))
    if len(unique_points) == 1:
        return unique_points[0]

    contains = _run_git(["branch", "-r", "--contains", "HEAD"])
    candidates = []
    for line in contains.stdout.splitlines():
        remote = line.strip().lstrip("*").strip()
        if remote.startswith("origin/") and "->" not in remote:
            candidates.append(remote.split("/", 1)[1])
    unique = sorted(set(candidates))
    if len(unique) == 1:
        return unique[0]
    raise ValueError("Detached HEAD; unable to determine a unique branch")


def _fallback_version_from_head() -> str:
    head = _run_git(["rev-parse", "HEAD"]).stdout.strip()
    return f"v100.0.{_short_sha(head)}"


def get_firmware_version() -> dict[str, Any]:
    branch = _current_branch()
    if branch != "release":
        return {"version": _fallback_version_from_head(), "branch": branch}
    try:
        version = _run_git(["describe", "--tags", "--abbrev=0"]).stdout.strip()
    except subprocess.CalledProcessError:
        version = _fallback_version_from_head()
    return {"version": version, "branch": branch}


def check_firmware_update() -> dict[str, Any]:
    _run_git(["fetch", "origin"])
    branch = _current_branch()
    if branch == "release":
        latest = _run_git(["describe", "--tags", "origin/release", "--abbrev=0"]).stdout.strip()
        try:
            current = _run_git(["describe", "--tags", "--abbrev=0"]).stdout.strip()
        except subprocess.CalledProcessError:
            current = _fallback_version_from_head()
        return {
            "current_version": current,
            "latest_version": latest,
            "needs_update": current != latest,
            "branch": branch,
        }

    remote_ref = f"origin/{branch}"
    _run_git(["rev-parse", "--verify", remote_ref])
    local_sha = _run_git(["rev-parse", "HEAD"]).stdout.strip()
    remote_sha = _run_git(["rev-parse", remote_ref]).stdout.strip()
    return {
        "current_version": _short_sha(local_sha),
        "latest_version": f"{branch}@{_short_sha(remote_sha)}",
        "needs_update": local_sha != remote_sha,
        "branch": branch,
    }


def perform_firmware_update() -> dict[str, Any]:
    old_commit = _run_git(["rev-parse", "HEAD"]).stdout.strip()
    try:
        _run_git(["reset", "--hard"])
        _run_git(["fetch", "origin"])
        branch = _current_branch()
        reset_ref = "origin/release" if branch == "release" else f"origin/{branch}"
        _run_git(["reset", "--hard", reset_ref])
        subprocess.run(["sudo", "./update.sh"], cwd=REPO_DIR, check=True)
    except Exception as exc:
        try:
            _run_git(["reset", "--hard", old_commit])
        except Exception as rollback_exc:
            raise RuntimeError(
                f"Update failed and rollback failed: {rollback_exc}"
            ) from exc
        raise RuntimeError("Update failed; rolled back to previous version") from exc

    try:
        with open("/tmp/firmware_updated.flag", "w", encoding="utf-8") as file:
            file.write("")
    except OSError:
        pass
    return {"message": "Update successful", **get_firmware_version()}

