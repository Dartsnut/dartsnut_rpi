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

setup_uv_project() {
    ensure_uv_installed
    cd "${REPO_DIR}"
    "${UV_BIN}" sync
}
