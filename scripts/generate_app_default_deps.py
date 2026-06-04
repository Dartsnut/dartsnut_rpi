#!/usr/bin/env python3
"""Generate default app pyproject.toml templates from sibling game/widget repos."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAMES_REPO = REPO_ROOT.parent / "python_games"
DEFAULT_WIDGETS_REPO = REPO_ROOT.parent / "dartsnut_widgets"
OUTPUT_DIR = REPO_ROOT / "core" / "app_defaults"
FIRMWARE_PYPROJECT = REPO_ROOT / "pyproject.toml"

GAMES_EXCLUDE_PARTS = {"old", "util", "sounds", "docs"}
WIDGETS_EXCLUDE_PARTS = {"apps", "fonts", "font_generator"}

# import root -> PyPI distribution name(s)
IMPORT_TO_DIST: dict[str, str | list[str]] = {
    "PIL": "pillow",
    "cv2": "opencv-python",
    "numpy": "numpy",
    "pygame": "pygame-ce",
    "pydartsnut": "pydartsnut",
    "aiohttp": "aiohttp",
    "websockets": "websockets",
    "evdev": "evdev",
    "requests": "requests",
}

# Packages always pulled in with aiohttp from firmware lockfile
AIOHTTP_TRANSITIVE = {
    "aiohappyeyeballs",
    "aiohttp",
    "aiosignal",
    "attrs",
    "certifi",
    "frozenlist",
    "idna",
    "multidict",
    "propcache",
    "typing_extensions",
    "yarl",
}

# Not needed by active games (legacy old/ only or unused on device)
GAMES_EXCLUDED_PACKAGES = {"opencv-python", "websockets", "pydub"}

# Dev-only paths: imports here are ignored even if under an app folder
DEV_ONLY_SUFFIXES = (
    "/util/",
    "/sounds/",
    "/test_",
    "/tests/",
)


def _parse_firmware_deps() -> dict[str, str]:
    text = FIRMWARE_PYPROJECT.read_text(encoding="utf-8")
    deps: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r'\s*"([^=]+)==([^"]+)"', line)
        if m:
            deps[m.group(1).lower()] = f"{m.group(1)}=={m.group(2)}"
    return deps


def _parse_requirements_txt(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>!]", line, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def _is_app_dir(path: Path) -> bool:
    return (path / "conf.json").is_file() and (path / "main.py").is_file()


def _iter_app_dirs(repo: Path, exclude_parts: set[str]) -> list[Path]:
    if not repo.is_dir():
        return []
    apps: list[Path] = []
    for child in sorted(repo.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in exclude_parts:
            continue
        if _is_app_dir(child):
            apps.append(child)
    return apps


def _collect_py_files(app_dir: Path) -> list[Path]:
    files: list[Path] = []
    for py in app_dir.rglob("*.py"):
        rel = py.relative_to(app_dir).as_posix()
        if any(part.startswith("test") for part in py.parts):
            continue
        files.append(py)
    return files


def _import_roots_from_file(path: Path) -> set[str]:
    roots: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return roots
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _scan_repo(repo: Path, exclude_parts: set[str]) -> set[str]:
    dists: set[str] = set()
    for app_dir in _iter_app_dirs(repo, exclude_parts):
        for py in _collect_py_files(app_dir):
            rel = py.as_posix()
            if any(marker in rel for marker in DEV_ONLY_SUFFIXES):
                continue
            for root in _import_roots_from_file(py):
                mapped = IMPORT_TO_DIST.get(root)
                if mapped is None:
                    continue
                if isinstance(mapped, list):
                    dists.update(mapped)
                else:
                    dists.add(mapped)
    return dists


def _resolve_deps(
    discovered: set[str],
    firmware_deps: dict[str, str],
    extra_from_requirements: set[str] | None = None,
) -> list[str]:
    names = set(discovered)
    if extra_from_requirements:
        names.update(extra_from_requirements)
    if "aiohttp" in names:
        names.update(AIOHTTP_TRANSITIVE)

    resolved: list[str] = []
    for name in sorted(names, key=str.lower):
        key = name.lower()
        if key in firmware_deps:
            resolved.append(firmware_deps[key])
        else:
            raise SystemExit(
                f"Package {name!r} not found in firmware pyproject.toml; "
                "add it there or update IMPORT_TO_DIST."
            )
    return resolved


def _render_pyproject(name: str, dependencies: list[str]) -> str:
    lines = [
        "# Generated by scripts/generate_app_default_deps.py — do not edit by hand.",
        "[project]",
        f'name = "{name}"',
        'version = "0.0.0"',
        'requires-python = ">=3.11"',
        "dependencies = [",
    ]
    for dep in dependencies:
        lines.append(f'    "{dep}",')
    lines.extend(
        [
            "]",
            "",
            "[tool.uv]",
            "package = false",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-repo", type=Path, default=DEFAULT_GAMES_REPO)
    parser.add_argument("--widgets-repo", type=Path, default=DEFAULT_WIDGETS_REPO)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    firmware_deps = _parse_firmware_deps()

    game_dists = _scan_repo(args.games_repo, GAMES_EXCLUDE_PARTS)
    game_dists -= GAMES_EXCLUDED_PACKAGES
    games_req = _parse_requirements_txt(args.games_repo / "requirements.txt")
    games_req -= GAMES_EXCLUDED_PACKAGES
    game_deps = _resolve_deps(game_dists, firmware_deps, games_req)

    widget_dists = _scan_repo(args.widgets_repo, WIDGETS_EXCLUDE_PARTS)
    widget_deps = _resolve_deps(widget_dists, firmware_deps)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "game_pyproject.toml").write_text(
        _render_pyproject("dartsnut-game-default", game_deps),
        encoding="utf-8",
    )
    (args.output_dir / "widget_pyproject.toml").write_text(
        _render_pyproject("dartsnut-widget-default", widget_deps),
        encoding="utf-8",
    )

    print(f"Wrote {args.output_dir / 'game_pyproject.toml'} ({len(game_deps)} deps)")
    print(f"Wrote {args.output_dir / 'widget_pyproject.toml'} ({len(widget_deps)} deps)")
    for dep in game_deps:
        print(f"  game: {dep}")
    for dep in widget_deps:
        print(f"  widget: {dep}")


if __name__ == "__main__":
    main()
