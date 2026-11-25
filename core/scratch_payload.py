"""Validation and scaffolding for Scratch 3 (.sb3) game payloads.

Pure file work over a staged payload directory: no devices, no network, no
runtime. That keeps it usable from the install path in ``core`` without
inverting the layering rule that ``runtime`` may import ``core`` but never the
reverse.

The contract this enforces, decided in the Scratch runtime design:

- A payload carries exactly one ``.sb3`` at its root, plus nothing else that
  matters. Zero or several is a hard failure, so an ambiguous payload is refused
  rather than resolved by directory order.
- The archive must open and contain a parseable ``project.json`` with targets.
- No extension may be declared that a device cannot provide. Speech,
  translation, and video sensing need a network endpoint or a camera. Refusing
  at install means an author learns immediately instead of a player finding out.
  Pen and music work and are allowed.
- Custom extension URLs are refused: they are fetched at load time and a device
  may have no network.

The firmware, not the uploader, writes ``main.py`` and ``pyproject.toml``. That
is what makes a payload just a Scratch project, and it removes the channel a
user-supplied ``main.py`` would otherwise open.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from typing import Any

_log = logging.getLogger(__name__)

DEFAULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_defaults")
SHIM_FILENAME = "sb3_main.py"
PYPROJECT_FILENAME = "sb3_pyproject.toml"
PROJECT_MANIFEST = "project.json"

# Keys in a project manifest's "extensions" array, which are scratch-vm's builtin
# extension ids (extension-support/extension-manager.js) rather than the internal
# scratch3_* module names.
UNSUPPORTED_EXTENSIONS = frozenset({
    "text2speech",
    "translate",
    "videoSensing",
    # music is refused, not merely silent: there is no audio context here, and
    # scratch3_music throws "No Audio Context Detected" from the step loop when
    # a note block runs. That kills the runner rather than no-opping, so a
    # project using music would install cleanly and then die on launch.
    "music",
})


class ScratchPayloadError(ValueError):
    """A payload is not a runnable Scratch project. Raised at install time."""


def _sb3_entries(directory: str) -> list[str]:
    try:
        return sorted(
            name for name in os.listdir(directory)
            if name.lower().endswith(".sb3") and not name.startswith("._")
        )
    except OSError as e:
        raise ScratchPayloadError(f"cannot read payload directory: {e}") from e


def is_scratch_payload(directory: str) -> bool:
    """True when a staged payload contains a Scratch project at all.

    A Python game payload has no .sb3 and must keep flowing through the existing
    install path untouched.
    """
    try:
        return any(
            name.lower().endswith(".sb3")
            for name in os.listdir(directory)
            if not name.startswith("._")
        )
    except OSError:
        return False


def find_project_file(directory: str) -> str:
    """Return the single .sb3 at ``directory``'s root."""
    entries = _sb3_entries(directory)
    if not entries:
        raise ScratchPayloadError("payload contains no .sb3 project")
    if len(entries) > 1:
        raise ScratchPayloadError(
            f"payload contains {len(entries)} .sb3 projects, expected exactly one: "
            + ", ".join(entries)
        )
    return os.path.join(directory, entries[0])


def read_project_manifest(sb3_path: str) -> dict[str, Any]:
    """Read and parse project.json out of an .sb3 archive."""
    name = os.path.basename(sb3_path)
    try:
        with zipfile.ZipFile(sb3_path) as archive:
            try:
                raw = archive.read(PROJECT_MANIFEST)
            except KeyError as e:
                raise ScratchPayloadError(f"{name} has no {PROJECT_MANIFEST}") from e
    except ScratchPayloadError:
        raise
    except (zipfile.BadZipFile, OSError) as e:
        raise ScratchPayloadError(f"{name} is not a valid .sb3: {e}") from e

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ScratchPayloadError(f"{name} has an unreadable {PROJECT_MANIFEST}: {e}") from e
    if not isinstance(manifest, dict):
        raise ScratchPayloadError(f"{name} manifest is not an object")
    if not isinstance(manifest.get("targets"), list) or not manifest["targets"]:
        raise ScratchPayloadError(f"{name} manifest has no targets")
    return manifest


def declared_extensions(manifest: dict[str, Any]) -> set[str]:
    """Extension ids a project declares. Unknown shapes are ignored, not fatal."""
    declared = manifest.get("extensions")
    if not isinstance(declared, list):
        return set()
    return {str(item) for item in declared if item}


def unsupported_extensions(manifest: dict[str, Any]) -> set[str]:
    """The declared extensions this device cannot provide."""
    return declared_extensions(manifest) & UNSUPPORTED_EXTENSIONS


def custom_extension_urls(manifest: dict[str, Any]) -> list[str]:
    """Custom extension URLs, which cannot load without network."""
    urls = manifest.get("extensionURLs")
    if not isinstance(urls, dict):
        return []
    return [str(value) for value in urls.values() if value]


# A costume SVG needs a viewBox. Without one, the renderer measures the SVG's
# real bounds by running it in a sandboxed frame, and that frame cannot answer
# here (no cross-frame postMessage). The result is a 30-second timeout per
# costume and then a blank costume, so a project would install cleanly and play
# visually wrong. Scratch 3 always writes a viewBox, so this only affects
# hand-made or converted artwork -- and deriving it from the declared width and
# height is what those projects mean.
_SVG_TAG = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_ATTR_VIEWBOX = re.compile(rb"\bviewBox\s*=", re.IGNORECASE)
# Accepts unitless, px, and the common absolute units. A unit-bearing value is
# still a valid viewBox size: at 96 dpi one pt is 1.333 px, and Scratch only ever
# compares these numbers against each other for aspect, so the raw value is the
# honest thing to carry across rather than a lossy conversion.
_ATTR_WIDTH = re.compile(
    rb"\bwidth\s*=\s*[\"']([0-9.]+)\s*(?:px|pt|pc|in|cm|mm)?[\"']", re.IGNORECASE
)
_ATTR_HEIGHT = re.compile(
    rb"\bheight\s*=\s*[\"']([0-9.]+)\s*(?:px|pt|pc|in|cm|mm)?[\"']", re.IGNORECASE
)


def _add_viewbox(svg_bytes: bytes) -> bytes | None:
    """Return the SVG with a derived viewBox, or None when it cannot be derived.

    Idempotent: an SVG that already declares one is returned unchanged.
    """
    tag_match = _SVG_TAG.search(svg_bytes)
    if not tag_match:
        return None
    tag = tag_match.group(0)
    if _ATTR_VIEWBOX.search(tag):
        return svg_bytes
    width = _ATTR_WIDTH.search(tag)
    height = _ATTR_HEIGHT.search(tag)
    if not width or not height:
        return None
    w, h = width.group(1), height.group(1)
    new_tag = tag[:-1].rstrip() + b' viewBox="0 0 ' + w + b" " + h + b'">'
    return svg_bytes[:tag_match.start()] + new_tag + svg_bytes[tag_match.end():]


def normalize_vector_costumes(sb3_path: str, manifest: dict[str, Any]) -> int:
    """Rewrite costume SVGs that lack a viewBox, in place. Returns how many changed.

    The whole archive is rewritten rather than patched, because a ZIP cannot grow
    a member in place. Entries other than the changed SVGs are copied byte for
    byte, so a project that needs no change is not rewritten at all.
    """
    svg_names = {
        str(costume.get("md5ext") or "")
        for target in manifest.get("targets", [])
        if isinstance(target, dict)
        for costume in (target.get("costumes") or [])
        if isinstance(costume, dict)
        and (str(costume.get("md5ext") or "").lower().endswith(".svg")
             or str(costume.get("dataFormat") or "").lower() == "svg")
    }
    svg_names.discard("")
    if not svg_names:
        return 0

    replacements: dict[str, bytes] = {}
    unmeasurable: list[str] = []
    with zipfile.ZipFile(sb3_path) as archive:
        available = set(archive.namelist())
        for name in sorted(svg_names & available):
            original = archive.read(name)
            updated = _add_viewbox(original)
            if updated is None:
                unmeasurable.append(name)
            elif updated != original:
                replacements[name] = updated

    if unmeasurable:
        raise ScratchPayloadError(
            "costume artwork has no viewBox and no width/height to derive one from, "
            "so this device cannot size it: " + ", ".join(unmeasurable[:3])
        )
    if not replacements:
        return 0

    directory = os.path.dirname(os.path.abspath(sb3_path))
    fd, temp_path = tempfile.mkstemp(prefix=".normalized.", suffix=".sb3", dir=directory)
    os.close(fd)
    try:
        with zipfile.ZipFile(sb3_path) as source, zipfile.ZipFile(temp_path, "w") as target:
            for item in source.infolist():
                data = replacements.get(item.filename)
                target.writestr(item, data if data is not None else source.read(item.filename))
        os.replace(temp_path, sb3_path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    _log.info("scratch payload: derived a viewBox for %d costume(s)", len(replacements))
    return len(replacements)


def validate_payload(directory: str) -> str:
    """Validate a staged payload directory; return the project file path.

    Raises ScratchPayloadError with a message meant for a log line, so an install
    failure is diagnosable without shell access to the device.
    """
    sb3_path = find_project_file(directory)
    manifest = read_project_manifest(sb3_path)
    refused = unsupported_extensions(manifest)
    if refused:
        raise ScratchPayloadError(
            "project uses extensions this device does not support: "
            + ", ".join(sorted(refused))
        )
    urls = custom_extension_urls(manifest)
    if urls:
        raise ScratchPayloadError(
            "project loads custom extensions, which a device cannot fetch: "
            + ", ".join(urls[:3])
        )
    # Fix vector costumes in place before the payload is accepted, so what the
    # device runs is what was validated.
    normalize_vector_costumes(sb3_path, manifest)
    return sb3_path


def scaffold_game_payload(directory: str) -> None:
    """Write the firmware-owned main.py and pyproject.toml into a payload.

    Overwrites unconditionally: these files belong to the firmware, and a payload
    that shipped its own must not be able to keep it.
    """
    shim_src = os.path.join(DEFAULTS_DIR, SHIM_FILENAME)
    pyproject_src = os.path.join(DEFAULTS_DIR, PYPROJECT_FILENAME)
    try:
        shutil.copy2(shim_src, os.path.join(directory, "main.py"))
        shutil.copy2(pyproject_src, os.path.join(directory, "pyproject.toml"))
    except OSError as e:
        raise ScratchPayloadError(f"cannot write launch scaffolding: {e}") from e
    _log.info("scratch payload: wrote launch scaffolding into %s", directory)


def prepare_payload(directory: str) -> str | None:
    """Validate and scaffold a staged payload if it is a Scratch project.

    Returns the project path when the payload is Scratch, or None when it is not
    (a Python game keeps the existing path). Raises ScratchPayloadError when the
    payload claims to be Scratch but is not runnable.
    """
    if not is_scratch_payload(directory):
        return None
    sb3_path = validate_payload(directory)
    scaffold_game_payload(directory)
    return sb3_path
