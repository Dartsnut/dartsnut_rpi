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
import tarfile
import tempfile
import threading
import time
import urllib.request


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

# Node lives beside the runtime, for the same reason: `git reset --hard` during
# a firmware update must not touch it, and it must survive between releases.
NODE_ROOT = os.environ.get("DARTSNUT_NODE_ROOT", "/var/lib/dartsnut/node")

# Pinned exactly. The gl prebuild is built for a specific ABI: v9.0.0-rc.10
# publishes node-v115 (Node 20) and node-v127 (Node 22) but nothing for v137
# (Node 24), so an unannounced major bump would break the install rather than
# silently degrade.
NODE_VERSION = "20.19.2"
NODE_LINUX_ARM64_SHA256 = "0d0c4a1c3a5aa657b76873eaa962936c7dc7a45047bd3957322544967713dc72"
NODE_MIRROR = "https://nodejs.org/dist"

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


def _node_root() -> str:
    return NODE_ROOT


def _vendored_node() -> str:
    return os.path.join(_node_root(), "bin", "node")


def _node_bin() -> str:
    """The Node binary to run: the vendored one when present, else PATH.

    Vendored rather than apt because the Debian closure is what makes a
    firmware update slow: nodejs+npm is 154 packages and forces a full apt index
    refresh (149 MB of indices), while the official tarball is one file and
    24 MB. (build-essential is not part of that saving: it arrives anyway via
    libgirepository1.0-dev -> gobject-introspection-bin.) The tarball is a plain
    directory with the same layout, so pointing at its bin/ is the whole
    integration.
    """
    override = os.environ.get("DARTSNUT_NODE_BIN")
    if override:
        return override
    return _vendored_node() if os.path.isfile(_vendored_node()) else "node"


def _npm_bin() -> str:
    override = os.environ.get("DARTSNUT_NPM_BIN")
    if override:
        return override
    vendored = os.path.join(_node_root(), "bin", "npm")
    return vendored if os.path.isfile(vendored) else "npm"


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
    """True when the runtime is present and matches this firmware and Node ABI.

    Requires the vendored Node, not just a valid stamp. A device upgrading from
    a release that installed Node via apt has a valid stamp but no
    /var/lib/dartsnut/node, and without this it would keep running on the apt
    binary forever -- which is the dependency this provisioning exists to drop.
    """
    if not node_installed():
        return False
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
    # Keep npm's staging inside the runtime dir. The device's /tmp is a 454 MB
    # tmpfs and this tree unpacks to ~324 MB, so inheriting TMPDIR=/tmp makes
    # the install fail with a truncated package.json rather than a clear
    # "no space left". Also point the cache here: root's ~/.npm is another 90 MB
    # on the SD card that a reinstall does not need to re-download.
    env = dict(os.environ)
    env["TMPDIR"] = directory
    env["npm_config_cache"] = os.path.join(directory, ".npm-cache")
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
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


def node_installed() -> bool:
    """True when the *vendored* Node runs and reports an ABI.

    Deliberately not `node_abi()` alone: a device that still has apt's
    /usr/bin/node would satisfy that, and the whole point of vendoring is to
    stop depending on it.
    """
    if not os.path.isfile(_vendored_node()):
        return False
    return bool(node_abi())


def _download_file(url: str, destination: str, expected_sha256: str) -> None:
    """Fetch url to destination, refusing anything whose sha256 differs.

    Logs progress: this is a 24 MB fetch on links that can take minutes, and an
    install that looks hung is indistinguishable from a working one otherwise.
    """
    digest = hashlib.sha256()
    received = 0
    last_log = time.monotonic()
    with urllib.request.urlopen(url, timeout=60) as response, open(destination, "wb") as out:
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
            received += len(chunk)
            if time.monotonic() - last_log >= 10:
                _log.info("scratch runtime: downloading Node %d KiB", received // 1024)
                last_log = time.monotonic()
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"checksum mismatch for {url}: expected {expected_sha256}, got {actual}"
        )


def _remove_stale_node_staging() -> None:
    """Delete staging dirs a killed install left beside NODE_ROOT.

    `tempfile.TemporaryDirectory` cleans up on exception but not on SIGKILL,
    which is exactly what the systemd restart in update.sh does to a running
    install. Observed on a device: two orphaned `tmp??????/` dirs holding 13 MB
    and 8 MB of a partial tarball. On a 14 GB eMMC that accumulates.
    """
    parent = os.path.dirname(_node_root()) or _node_root()
    try:
        entries = os.listdir(parent)
    except OSError:
        return
    for entry in entries:
        # TemporaryDirectory names are tmpXXXXXXXX; nothing else we create here
        # uses that prefix, and both are siblings of node/.
        if not entry.startswith("tmp") or not os.path.isdir(os.path.join(parent, entry)):
            continue
        path = os.path.join(parent, entry)
        # Only remove it if it looks like our staging area, never an unrelated
        # directory that happens to start with "tmp".
        try:
            contents = os.listdir(path)
        except OSError:
            continue
        if not any(name.endswith(".tar.xz") or name == "unpacked" for name in contents):
            continue
        shutil.rmtree(path, ignore_errors=True)
        _log.info("scratch runtime: removed stale Node staging dir %s", path)


def ensure_node() -> bool:
    """Install the pinned Node tarball into NODE_ROOT. Idempotent.

    One 24 MB file and one sequential extract, versus 154 apt packages and a
    forced index refresh for nodejs+npm. The download is a plain directory
    (bin/, lib/, include/), extracted at the root so `bin/node` resolves.

    Verified by checksum before extraction: this is the one artifact we fetch
    from a third party at runtime, and it becomes an executable we launch.
    """
    if node_installed():
        return True
    _remove_stale_node_staging()
    root = _node_root()
    name = f"node-v{NODE_VERSION}-linux-arm64"
    url = f"{NODE_MIRROR}/v{NODE_VERSION}/{name}.tar.xz"
    started = time.monotonic()
    try:
        os.makedirs(root, exist_ok=True)
        # Stage beside the destination, not in /tmp: /tmp is a 454 MB tmpfs on
        # the device and this archive plus its unpacked tree would overflow it.
        staging_parent = os.path.dirname(root) or root
        os.makedirs(staging_parent, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging_parent) as tmp:
            archive = os.path.join(tmp, f"{name}.tar.xz")
            _download_file(url, archive, NODE_LINUX_ARM64_SHA256)
            staging = os.path.join(tmp, "unpacked")
            os.makedirs(staging)
            with tarfile.open(archive, "r:xz") as tar:
                # `data` filters absolute paths and escapes out. Python 3.14
                # makes this the default; being explicit keeps 3.11-3.13 quiet.
                try:
                    tar.extractall(staging, filter="data")
                except TypeError:
                    tar.extractall(staging)
            # Strip the top-level directory so NODE_ROOT/bin/node is the binary.
            inner = os.path.join(staging, name)
            source = inner if os.path.isdir(inner) else staging
            for entry in os.listdir(source):
                shutil.move(os.path.join(source, entry), os.path.join(root, entry))
    except Exception as e:
        _log.error("scratch runtime: cannot install Node %s: %s", NODE_VERSION, e)
        # A failed attempt must not leave a half-populated root behind: the ABI
        # probe would keep saying "not installed" while bin/ sits empty, and the
        # next attempt would merge into that debris.
        shutil.rmtree(root, ignore_errors=True)
        return False
    if not node_installed():
        _log.error(
            "scratch runtime: Node %s installed but does not report an ABI", NODE_VERSION
        )
        return False
    _log.info(
        "scratch runtime: installed node %s elapsed=%.1fs dir=%s",
        NODE_VERSION,
        time.monotonic() - started,
        root,
    )
    return True


def ensure_installed(*, force: bool = False) -> bool:
    """Provision Node and the runtime if missing or stale. Idempotent.

    Copies the bundled sources, runs the pinned install, then writes the stamp.
    The stamp is written last so an interrupted install never looks complete.
    """
    if not force and is_installed():
        return True
    if not ensure_node():
        return False
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
