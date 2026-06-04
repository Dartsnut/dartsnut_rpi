#!/bin/bash

UV_HOME="/root"
UV_BIN="${UV_HOME}/.local/bin/uv"

activate_uv_path() {
    export PATH="${UV_HOME}/.local/bin:${PATH}"
    if [ -f "${UV_HOME}/.local/bin/env" ]; then
        # shellcheck source=/dev/null
        source "${UV_HOME}/.local/bin/env"
    fi
}

ensure_uv_installed() {
    activate_uv_path

    if [ -x "${UV_BIN}" ]; then
        export UV_BIN
        return 0
    fi

    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
        export UV_BIN
        return 0
    fi

    echo "Installing uv to ${UV_HOME}/.local/bin..."
    curl -LsSf https://astral.sh/uv/install.sh | env HOME="${UV_HOME}" sh
    activate_uv_path

    if [ -x "${UV_BIN}" ]; then
        export UV_BIN
        return 0
    fi

    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
        export UV_BIN
        return 0
    fi

    echo "Error: uv installation failed"
    exit 1
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
    ensure_uv_installed
    cd "${REPO_DIR}"
    "${UV_BIN}" sync
}

setup_uv_project() {
    sync_uv_project
    verify_uv_python_packages
}

refresh_uv_project() {
    ensure_uv_installed
    rm -rf "${REPO_DIR}/venv0" "${REPO_DIR}/.venv"
    sync_uv_project
    verify_uv_python_packages
}
