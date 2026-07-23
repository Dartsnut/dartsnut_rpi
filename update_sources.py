"""Latency-based source selection for firmware Git and uv updates."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

DIRECT_GITHUB_HOST = "github.com"
GITHUB_PROXY_HOST = "v4.gh-proxy.org"
GITHUB_PROXY_PREFIX = "https://v4.gh-proxy.org/https://"
GITHUB_REWRITE_KEY = f"url.{GITHUB_PROXY_PREFIX}.insteadOf"
GITHUB_REWRITE_VALUE = "https://"

DIRECT_PYPI_HOST = "pypi.org"
USTC_PYPI_HOST = "mirrors.ustc.edu.cn"
DIRECT_PYPI_INDEX = "https://pypi.org/simple"
USTC_PYPI_INDEX = "https://mirrors.ustc.edu.cn/pypi/simple"

_PING_AVERAGE_RE = re.compile(
    r"(?:rtt|round-trip)[^=]*=\s*[^/]+/([^/]+)/[^/]+/[^\s]+\s+ms"
)


def _log(message: str) -> None:
    print(f"Update sources: {message}", file=sys.stderr)


def parse_ping_average(output: str) -> float | None:
    """Return the average latency from a Linux ping summary."""
    match = _PING_AVERAGE_RE.search(output)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def ping_average(host: str) -> float | None:
    """Ping a host three times and return its reported average latency."""
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "2", host],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"ping failed for {host}: {exc}")
        return None

    average = parse_ping_average(
        f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}"
    )
    if average is None:
        _log(f"no usable ping result for {host}")
    else:
        _log(f"{host} average latency {average:.3f} ms")
    return average


def choose_by_latency(
    direct_host: str,
    mirror_host: str,
    *,
    label: str,
) -> str:
    """Return ``direct`` or ``mirror``; direct wins ties and total failure."""
    direct_latency = ping_average(direct_host)
    mirror_latency = ping_average(mirror_host)

    if mirror_latency is not None and (
        direct_latency is None or mirror_latency < direct_latency
    ):
        selection = "mirror"
    else:
        selection = "direct"

    _log(f"selected {label} {selection} source")
    return selection


def _unwrapped_origin_url(url: str) -> str:
    if url.startswith(GITHUB_PROXY_PREFIX):
        return url[len("https://v4.gh-proxy.org/") :]
    return url


def is_https_github_origin(url: str) -> bool:
    """Whether an origin URL is HTTPS and targets exactly github.com."""
    parsed = urlsplit(_unwrapped_origin_url(url.strip()))
    return parsed.scheme.lower() == "https" and parsed.hostname == DIRECT_GITHUB_HOST


def _remove_managed_git_rewrite() -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "config",
                "--global",
                "--unset-all",
                GITHUB_REWRITE_KEY,
                GITHUB_REWRITE_VALUE,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        _log(f"unable to remove GitHub proxy rewrite: {exc}")
        return False

    # Git returns 5 when the requested key/value does not exist.
    if result.returncode not in (0, 5):
        detail = (getattr(result, "stderr", "") or "").strip()
        _log(
            "unable to remove GitHub proxy rewrite"
            + (f": {detail}" if detail else f" (exit {result.returncode})")
        )
        return False
    return True


def _enable_managed_git_rewrite() -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "config",
                "--global",
                "--replace-all",
                GITHUB_REWRITE_KEY,
                GITHUB_REWRITE_VALUE,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        _log(f"unable to configure GitHub proxy rewrite: {exc}")
        _remove_managed_git_rewrite()
        return False

    if result.returncode == 0:
        return True

    detail = (getattr(result, "stderr", "") or "").strip()
    _log(
        "unable to configure GitHub proxy rewrite"
        + (f": {detail}" if detail else f" (exit {result.returncode})")
    )
    _remove_managed_git_rewrite()
    return False


def _prepare_git_source(repo_cwd: str) -> str:
    """Configure the managed GitHub rewrite and return the selected route."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        _log(f"unable to read origin URL; using direct Git route: {exc}")
        _remove_managed_git_rewrite()
        return "direct"

    origin_url = (getattr(result, "stdout", "") or "").strip()
    if result.returncode != 0 or not is_https_github_origin(origin_url):
        if result.returncode != 0:
            _log("unable to read origin URL; skipping GitHub latency checks")
        else:
            _log(f"origin is not HTTPS GitHub ({origin_url}); skipping GitHub latency checks")
        _remove_managed_git_rewrite()
        return "direct"

    selection = choose_by_latency(
        DIRECT_GITHUB_HOST,
        GITHUB_PROXY_HOST,
        label="Git",
    )
    if selection == "mirror":
        if _enable_managed_git_rewrite():
            _log("enabled GitHub proxy rewrite")
            return "mirror"
        _log("falling back to direct Git route")
        return "direct"

    _remove_managed_git_rewrite()
    return "direct"


def prepare_git_source(repo_cwd: str) -> str:
    """Prepare Git routing without ever blocking the caller's update."""
    try:
        return _prepare_git_source(repo_cwd)
    except Exception as exc:
        _log(f"unexpected Git source-selection error; using direct route: {exc}")
        try:
            _remove_managed_git_rewrite()
        except Exception:
            pass
        return "direct"


def select_uv_index() -> str:
    """Choose the PyPI index used by uv for this update process."""
    selection = choose_by_latency(
        DIRECT_PYPI_HOST,
        USTC_PYPI_HOST,
        label="uv",
    )
    if selection == "mirror":
        return USTC_PYPI_INDEX
    return DIRECT_PYPI_INDEX


def prepare_update_sources(repo_cwd: str) -> str:
    """Prepare Git routing and return the selected uv default index."""
    try:
        prepare_git_source(repo_cwd)
    except Exception as exc:  # Source selection must never block updates.
        _log(f"unexpected Git source-selection error; using direct route: {exc}")
        try:
            _remove_managed_git_rewrite()
        except Exception:
            pass

    try:
        return select_uv_index()
    except Exception as exc:  # Source selection must never block updates.
        _log(f"unexpected uv source-selection error; using PyPI: {exc}")
        return DIRECT_PYPI_INDEX


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["select-uv"]:
        print(select_uv_index())
        return 0
    print("usage: update_sources.py select-uv", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
