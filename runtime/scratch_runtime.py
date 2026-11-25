"""Shared Scratch 3 (sb3) runtime: provisioning, readiness, and payload validation.

One runtime per device, not per game. It lives outside the repository checkout
so a firmware update (a ``git pull``) cannot clobber or stale it, and its
dependencies are pinned exactly so an unattended install can never resolve
to a version that was never tested.

"Installed" is a stamp, not a directory listing: the stamp hashes the bundled
sources and records the Node ABI the native WebGL binding was built for. A
``webgl.node`` left behind by a previous Node version passes a file-exists check
and then throws at require time, which is exactly the failure the ABI half of
the stamp exists to catch.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
import subprocess
import threading
import time


_log = logging.getLogger(__name__)

# Bundled runner sources, copied into the runtime directory at install time.
# They sit beside the installed node_modules so `require` resolves normally.
BUNDLED_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch")
BUNDLED_SOURCE_FILES = ("sb3_runner.js", "panel_geometry.js", "shm_abi.js", "dom_shim.js", "frame_clock.js")
MANIFEST_FILENAME = "package.json"
LOCKFILE_FILENAME = "package-lock.json"
STAMP_FILENAME = ".dartsnut_scratch_stamp"
STAMP_VERSION = "v1:"
NODE_MODULES_FILENAME = "node_modules"

# Runtime location. Outside the checkout on purpose: firmware updates replace
# the tree, user data does not.
RUNTIME_ROOT = os.environ.get("DARTSNUT_SCRATCH_RUNTIME_DIR", "/var/lib/dartsnut/scratch-runtime")

# The one file the Node runner needs from the Scratch project.
PROJECT_MANIFEST = "project.json"

# Extensions a Dartsnut device cannot provide. Refused at install rather than at
# launch so the author learns immediately instead of a player finding out.
#
# Keys are the builtin extension ids used in a project manifest's "extensions"
# array (scratch-vm extension-support/extension-manager.js), not the internal
# scratch3_* module names. pen and music are supported and are not listed.
UNSUPPORTED_EXTENSIONS = frozenset({
    "text2speech",   # needs a cloud TTS endpoint
    "translate",     # needs a cloud translation endpoint
    "videoSensing",  # needs a camera
})

_abi_cache: tuple[str | None, str | None] = (None, None)  # (node_bin, abi)

# Retries after a launch-triggered install fails. Boot provisioning used to
# retry forever, which is what made every device pay; this only runs after a
# user asked for a Scratch game, and gives up so a permanently offline device
# stops burning CPU on a game nobody is waiting for any more.
RETRY_BACKOFF_SECONDS = (5.0, 15.0, 30.0, 60.0, 120.0)

# Serializes the check-and-install within one process. A second caller would
# otherwise interleave two npm runs in the same directory, and npm writes
# node_modules incrementally.
_install_lock = threading.Lock()
# Guards _install_inflight only. Separate from _install_lock because that one is
# held for the whole ~35 s install, and the launch gate must never wait on it.
_install_guard = threading.Lock()
_install_inflight = threading.Event()
# Serializes installs across processes. update.sh restarts this service, and
# systemd's default KillMode=control-group SIGTERMs a running npm ci along with
# it; the restarted service would otherwise start a second npm ci in the same
# directory while the first is still being torn down. Held for the whole install,
# so it also makes an interrupted install finish or fail before the retry starts.
INSTALL_LOCK_FILENAME = ".dartsnut_scratch_install.lock"


class ScratchPayloadError(ValueError):
    """A payload is not a runnable Scratch project. Raised at install time."""


def runtime_dir() -> str:
    return RUNTIME_ROOT


def _node_bin() -> str:
    return os.environ.get("DARTSNUT_NODE_BIN", "node")


def _npm_bin() -> str:
    return os.environ.get("DARTSNUT_NPM_BIN", "npm")


def node_abi() -> str:
    """Return the running Node's module ABI (e.g. "115"), or "" when unavailable.

    Cached per binary path. Node processes are expensive to spawn and this is
    consulted on every install and every launch gate.
    """
    global _abi_cache
    node = _node_bin()
    if _abi_cache[0] == node:
        return _abi_cache[1] or ""
    abi = ""
    try:
        result = subprocess.run(
            [node, "-p", "process.versions.modules"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            abi = result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        _log.warning("scratch runtime: could not read node ABI: %s", e)
    # Cache successes only. A transient failure -- Node not yet on PATH during
    # early boot, or a package still being installed -- must be retried on the
    # next call, otherwise one bad probe disables every Scratch launch for the
    # life of the process.
    if abi:
        _abi_cache = (node, abi)
    return abi


def _stamp_path() -> str:
    return os.path.join(runtime_dir(), STAMP_FILENAME)


def _sources_fingerprint() -> str:
    """Hash the bundled runner sources plus the pinned manifest.

    Any change to the runner or its dependency pins invalidates an install, so a
    firmware update that ships a new runner rebuilds the runtime rather than
    running the old JavaScript against new Python.
    """
    digest = hashlib.sha256()
    for name in sorted(BUNDLED_SOURCE_FILES):
        path = os.path.join(BUNDLED_SOURCES_DIR, name)
        try:
            with open(path, "rb") as f:
                digest.update(name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(f.read())
                digest.update(b"\0")
        except OSError as e:
            _log.warning("scratch runtime: cannot read bundled source %s: %s", name, e)
            return ""
    for name in (MANIFEST_FILENAME, LOCKFILE_FILENAME):
        path = os.path.join(BUNDLED_SOURCES_DIR, name)
        try:
            with open(path, "rb") as f:
                digest.update(f.read())
                digest.update(b"\0")
        except OSError as e:
            _log.warning("scratch runtime: cannot read bundled %s: %s", name, e)
            return ""
    return digest.hexdigest()


def expected_stamp() -> str:
    """The stamp a current install would write, or "" when it cannot be computed."""
    fingerprint = _sources_fingerprint()
    if not fingerprint:
        return ""
    abi = node_abi()
    if not abi:
        # Without an ABI the stamp cannot describe what was built, so no stamp
        # can be trusted. Refusing here means "not installed" rather than
        # "installed for ABI ''", which would match any future Node.
        return ""
    return f"{STAMP_VERSION}{fingerprint}:{abi}"


def is_installed() -> bool:
    """True when the runtime is present and matches this firmware and Node ABI."""
    expected = expected_stamp()
    if not expected:
        return False
    if not os.path.isdir(os.path.join(runtime_dir(), NODE_MODULES_FILENAME)):
        return False
    try:
        with open(_stamp_path(), encoding="utf-8") as f:
            stored = f.read().strip()
    except OSError:
        return False
    return stored == expected


def installed_node_abi() -> str:
    """The ABI recorded at install time, or "" when unknown.

    Distinguishes "never installed" from "installed for a different Node", which
    is what the launch gate reports to the user.
    """
    try:
        with open(_stamp_path(), encoding="utf-8") as f:
            stored = f.read().strip()
    except OSError:
        return ""
    prefix = STAMP_VERSION
    if not stored.startswith(prefix):
        return ""
    rest = stored[len(prefix):]
    if ":" not in rest:
        return ""
    return rest.rsplit(":", 1)[1]


def _run_npm_install(directory: str) -> None:
    """Install exactly what the lockfile pins. Raises on a non-zero exit.

    `ci` rather than `install`: it installs from package-lock.json instead of
    resolving ranges fresh on every device, it fails loudly when the lock and
    manifest disagree, and it removes node_modules first so a changed pin set
    cannot leave a module behind that the fingerprint no longer describes.
    """
    command = [_npm_bin(), "ci", "--no-audit", "--no-fund"]
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
        raise RuntimeError(
            f"npm install failed ({result.returncode}): {' | '.join(tail)}"
        )


# Modules that must load before a stamp is written. Both have broken on a device
# by installing cleanly and then throwing at require time, which a stamp would
# certify as ready:
#
#   jsdom  a version needing a newer Node installs and then fails on require.
#   gl     its native binding is built by a prebuild download or node-gyp; when
#          that step leaves nothing behind, the package is present but the
#          binding is not, and the runner dies on its first `require('gl')`.
#
# Requiring `gl` does NOT need an X display -- only creating a context does, and
# that is the runner's job, not provisioning's. Verified on a device.
REQUIRED_MODULES = ("jsdom", "gl")


def _modules_load(directory: str) -> bool:
    """True when the installed tree can actually be required."""
    script = ";".join(f"require({name!r})" for name in REQUIRED_MODULES)
    try:
        result = subprocess.run(
            [_node_bin(), "-e", script],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        _log.error("scratch runtime: cannot run the load check: %s", e)
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[:3]
        _log.error(
            "scratch runtime: installed tree does not load (node %s): %s",
            node_abi() or "?",
            " | ".join(detail),
        )
        return False
    return True


def ensure_installed(*, force: bool = False) -> bool:
    """Provision the runtime if it is missing or stale. Idempotent.

    Copies the bundled sources, runs the pinned install, then writes the stamp.
    The stamp is written last so an interrupted install never looks complete.
    """
    if not force and is_installed():
        return True
    with _install_lock:
        # Another thread or process instance may have finished while we waited.
        if not force and is_installed():
            return True
        # Cross-process lock: the systemd restart in update.sh kills a running
        # npm ci, and the restarted service must not race the one being torn
        # down. Blocking here is safe -- this never runs on the display thread.
        try:
            os.makedirs(runtime_dir(), exist_ok=True)
            lock_file = open(os.path.join(runtime_dir(), INSTALL_LOCK_FILENAME), "a+")
        except OSError as e:
            _log.error("scratch runtime: cannot open install lock: %s", e)
            return False
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            # Re-check under the lock: whoever held it may have just finished.
            if not force and is_installed():
                return True
            return _install_locked()
        finally:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            finally:
                lock_file.close()


def _install_locked() -> bool:
    fingerprint = _sources_fingerprint()
    if not fingerprint:
        _log.error("scratch runtime: bundled sources are unreadable; cannot install")
        return False

    directory = runtime_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        for name in BUNDLED_SOURCE_FILES:
            shutil.copy2(os.path.join(BUNDLED_SOURCES_DIR, name), os.path.join(directory, name))
        for name in (MANIFEST_FILENAME, LOCKFILE_FILENAME):
            shutil.copy2(os.path.join(BUNDLED_SOURCES_DIR, name), os.path.join(directory, name))
    except OSError as e:
        _log.error("scratch runtime: cannot stage bundled sources: %s", e)
        return False

    # Drop the stamp before touching node_modules, not after a failure.
    #
    # `npm ci` deletes node_modules and rebuilds it, so between those two points
    # the runtime is unusable while the previous stamp still says it is ready. A
    # launch landing in that window starts the runner against a half-installed
    # tree and dies with "Could not locate the bindings file" -- observed on a
    # device at the moment an update was triggered. Removing the stamp first
    # makes the launch gate refuse the game and show "Preparing game..." instead.
    _remove_stamp()

    started = time.monotonic()
    try:
        _run_npm_install(directory)
    except Exception as e:
        _log.error("scratch runtime: install failed: %s", e)
        # Leave no stamp: the next launch retries from a clean expectation.
        return False

    # A tree that installs but cannot be required is worse than a failed
    # install: it would stamp as ready and fail on every launch instead.
    if not _modules_load(directory):
        return False

    stamp = expected_stamp()
    if not stamp:
        _remove_stamp()
        return False
    try:
        with open(_stamp_path(), "w", encoding="utf-8") as f:
            f.write(stamp)
    except OSError as e:
        _log.error("scratch runtime: cannot write stamp: %s", e)
        return False

    _log.info(
        "scratch runtime: installed node_abi=%s elapsed=%.1fs dir=%s",
        node_abi() or "(unknown)",
        time.monotonic() - started,
        directory,
    )
    return True


def _remove_stamp() -> None:
    try:
        os.remove(_stamp_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        _log.warning("scratch runtime: cannot remove stamp: %s", e)


def start_install_async(*, on_start=None, on_done=None) -> bool:
    """Install in the background; True when a worker started.

    False means an install is already running. Never call `ensure_installed`
    directly from the display thread: `npm ci` takes ~35 s and `_install_lock`
    would hold it there.

    A failed install is retried on RETRY_BACKOFF_SECONDS, because the usual
    cause is a device that is briefly offline, and the alternative -- one shot,
    then the user has to tap again blindly -- is worse than a bounded retry.
    `on_done(False)` is reported only once the retries are exhausted.

    `on_start()` runs once, before the first attempt, and `on_done(success)`
    once, after the last.
    """
    with _install_guard:
        if _install_inflight.is_set():
            return False
        _install_inflight.set()

    def worker() -> None:
        success = False
        try:
            if on_start is not None:
                try:
                    on_start()
                except Exception as e:
                    _log.warning("scratch runtime: on_start error: %s", e)
            for delay in RETRY_BACKOFF_SECONDS:
                try:
                    success = ensure_installed()
                except Exception as e:
                    _log.error("scratch runtime: background install error: %s", e)
                    success = False
                if success:
                    break
                _log.warning("scratch runtime: install failed; retrying in %.0fs", delay)
                time.sleep(delay)
            else:
                # The loop ran out without success; one final attempt so the last
                # backoff is not wasted on a device that came back mid-sleep.
                try:
                    success = ensure_installed()
                except Exception as e:
                    _log.error("scratch runtime: background install error: %s", e)
                    success = False
        finally:
            _install_inflight.clear()
            if on_done is not None:
                try:
                    on_done(success)
                except Exception as e:
                    _log.warning("scratch runtime: on_done error: %s", e)

    threading.Thread(target=worker, name="scratch-runtime-install", daemon=True).start()
    return True
