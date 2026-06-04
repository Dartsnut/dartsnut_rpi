#!/bin/bash

activate_uv_path() {
    if [ -f "${HOME}/.local/bin/env" ]; then
        # shellcheck source=/dev/null
        source "${HOME}/.local/bin/env"
    else
        export PATH="${HOME}/.local/bin:${PATH}"
    fi
}

ensure_uv_installed() {
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi

    activate_uv_path
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi

    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    activate_uv_path
    if ! command -v uv >/dev/null 2>&1; then
        echo "Error: uv installation failed"
        exit 1
    fi
}

setup_uv_project() {
    ensure_uv_installed
    cd "${REPO_DIR}"
    uv sync
}
