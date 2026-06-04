#!/bin/bash

ensure_uv_installed() {
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
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
