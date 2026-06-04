#!/bin/bash

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UV_BIN="${REPO_DIR}/uv"

resolve_uv_bin() {
    if [ ! -x "${UV_BIN}" ]; then
        echo "Error: vendored uv not found or not executable at ${UV_BIN}" >&2
        exit 1
    fi
    export UV_BIN
}

_pyproject_requirement() {
    local package="$1"
    grep -E "\"${package}==" "${REPO_DIR}/pyproject.toml" | sed -n 's/.*"\([^"]*\)".*/\1/p' | head -n 1
}

_verify_pygame_ce() {
    echo "Verifying pygame module (pygame-ce)..."
    if "${UV_BIN}" run python -c "import pygame; pygame.Surface" 2>/dev/null; then
        return 0
    fi
    echo "pygame import incomplete; force-reinstalling pygame-ce..."
    local req
    req="$(_pyproject_requirement pygame-ce)"
    req="${req:-pygame-ce}"
    "${UV_BIN}" pip install --force-reinstall --no-deps "${req}"
    "${UV_BIN}" run python -c "import pygame; pygame.Surface"
}

_verify_pybluez_dartsnut() {
    echo "Verifying bluetooth module (pybluez-dartsnut)..."
    if "${UV_BIN}" run python -c "import bluetooth; import bluetooth._bluetooth" 2>/dev/null; then
        return 0
    fi
    echo "bluetooth import failed; force-reinstalling pybluez-dartsnut..."
    local req
    req="$(_pyproject_requirement pybluez-dartsnut)"
    req="${req:-pybluez-dartsnut==0.30}"
    "${UV_BIN}" pip install --force-reinstall --no-deps "${req}"
    "${UV_BIN}" run python -c "import bluetooth; import bluetooth._bluetooth"
}

verify_uv_python_packages() {
    _verify_pygame_ce
    _verify_pybluez_dartsnut
}

sync_uv_project() {
    resolve_uv_bin
    cd "${REPO_DIR}"
    "${UV_BIN}" sync
}

setup_uv_project() {
    sync_uv_project
    verify_uv_python_packages
}

refresh_uv_project() {
    resolve_uv_bin
    rm -rf "${REPO_DIR}/venv0" "${REPO_DIR}/.venv"
    sync_uv_project
    verify_uv_python_packages
}
