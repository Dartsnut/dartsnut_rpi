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
    "${UV_BIN}" pip install --force-reinstall --no-deps "${req}" || return $?
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
    "${UV_BIN}" pip install --force-reinstall --no-deps "${req}" || return $?
    "${UV_BIN}" run python -c "import bluetooth; import bluetooth._bluetooth"
}

_verify_system_dbus() {
    echo "Verifying dbus module (python3-dbus)..."
    if "${UV_BIN}" run python -c "import dbus"; then
        return 0
    fi
    echo "dbus import failed; install/repair system package python3-dbus." >&2
    return 1
}

verify_uv_python_packages() {
    _verify_pygame_ce || return $?
    _verify_pybluez_dartsnut || return $?
    _verify_system_dbus
}

sync_uv_project() {
    resolve_uv_bin
    cd "${REPO_DIR}"

    local attempt
    local delay
    local max_attempts
    local package
    local status
    local uv_output
    uv_output="$(mktemp)"

    if [ ! -d "${REPO_DIR}/.venv" ]; then
        "${UV_BIN}" venv --system-site-packages || return $?
    fi

    max_attempts="${UV_SYNC_MAX_ATTEMPTS:-3}"
    if ! [[ "${max_attempts}" =~ ^[0-9]+$ ]] || [ "${max_attempts}" -lt 1 ]; then
        max_attempts=3
    fi

    package=""
    attempt=1
    while [ "${attempt}" -le "${max_attempts}" ]; do
        if [ -n "${package}" ]; then
            "${UV_BIN}" sync --refresh-package "${package}" >"${uv_output}" 2>&1
        else
            "${UV_BIN}" sync >"${uv_output}" 2>&1
        fi
        status=$?
        cat "${uv_output}"

        if [ "${status}" -eq 0 ]; then
            rm -f "${uv_output}"
            return 0
        fi

        if [ -z "${package}" ] && grep -q "The wheel is invalid: Metadata field Name not found" "${uv_output}"; then
            package="$(
                sed -n 's/.*Failed to install: .* (\([^=()[:space:]]*\)==.*/\1/p' "${uv_output}" | head -n 1
            )"
        fi

        if [ -n "${package}" ] && [ "${attempt}" -eq 1 ]; then
            echo "uv sync hit invalid cached wheel for ${package}; cleaning package cache and retrying..."
            "${UV_BIN}" cache clean "${package}" || return $?
        fi

        if [ "${attempt}" -ge "${max_attempts}" ]; then
            break
        fi

        delay=$((1 << attempt))
        echo "uv sync failed; retrying in ${delay}s (attempt $((attempt + 1))/${max_attempts})..."
        sleep "${delay}"
        attempt=$((attempt + 1))
    done

    rm -f "${uv_output}"
    return "${status}"
}

setup_uv_project() {
    sync_uv_project || return $?
    verify_uv_python_packages
}

refresh_uv_project() {
    resolve_uv_bin
    rm -rf "${REPO_DIR}/venv0" "${REPO_DIR}/.venv"
    sync_uv_project || return $?
    verify_uv_python_packages
}
