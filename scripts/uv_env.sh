#!/bin/bash

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UV_BIN="${REPO_DIR}/uv"
FORCEFSCK_PATH="${FORCEFSCK_PATH:-/forcefsck}"
DARTSNUT_UPDATE_PENDING_MARKERS="${DARTSNUT_UPDATE_PENDING_MARKERS:-/boot/firmware/dartsnut_update_pending /boot/dartsnut_update_pending /var/lib/dartsnut/update_pending}"
UV_LOCK_BACKUP=""
UV_LOCK_EXISTED=0


select_uv_default_index() {
    if [ -n "${UV_DEFAULT_INDEX:-}" ]; then
        export UV_DEFAULT_INDEX
        echo "Using configured uv index: ${UV_DEFAULT_INDEX}"
        return 0
    fi

    local selected
    if selected="$(python3 "${REPO_DIR}/update_sources.py" select-uv)" && [ -n "${selected}" ]; then
        UV_DEFAULT_INDEX="${selected}"
    else
        echo "Warning: uv source selection failed; using PyPI." >&2
        UV_DEFAULT_INDEX="https://pypi.org/simple"
    fi
    export UV_DEFAULT_INDEX
    echo "Selected uv index: ${UV_DEFAULT_INDEX}"
}

backup_uv_lock() {
    local lock_path="${REPO_DIR}/uv.lock"
    if [ -n "${UV_LOCK_BACKUP}" ]; then
        return 0
    fi

    UV_LOCK_BACKUP="$(mktemp)" || return $?
    if [ -f "${lock_path}" ]; then
        cp "${lock_path}" "${UV_LOCK_BACKUP}" || {
            rm -f "${UV_LOCK_BACKUP}"
            UV_LOCK_BACKUP=""
            return 1
        }
        UV_LOCK_EXISTED=1
    else
        UV_LOCK_EXISTED=0
    fi
}

restore_uv_lock() {
    local lock_path="${REPO_DIR}/uv.lock"
    if [ -z "${UV_LOCK_BACKUP}" ]; then
        return 0
    fi

    if [ "${UV_LOCK_EXISTED}" -eq 1 ]; then
        cp "${UV_LOCK_BACKUP}" "${lock_path}"
    else
        rm -f "${lock_path}"
    fi
    rm -f "${UV_LOCK_BACKUP}"
    UV_LOCK_BACKUP=""
    UV_LOCK_EXISTED=0
}

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
    _run_uv_with_root_cache_repair pip install --force-reinstall --no-deps "${req}" || return $?
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
    _run_uv_with_root_cache_repair pip install --force-reinstall --no-deps "${req}" || return $?
    "${UV_BIN}" run python -c "import bluetooth; import bluetooth._bluetooth"
}

_verify_bluezero() {
    echo "Verifying bluezero module..."
    "${UV_BIN}" run python -c "import bluezero"
}

_verify_system_dbus() {
    echo "Verifying dbus module (python3-dbus)..."
    if "${UV_BIN}" run python -c "import dbus"; then
        return 0
    fi
    echo "dbus import failed; install/repair system package python3-dbus." >&2
    return 1
}

_verify_system_gi() {
    echo "Verifying gi module (python3-gi)..."
    if "${UV_BIN}" run python -c "import gi"; then
        return 0
    fi
    echo "gi import failed; install/repair system package python3-gi." >&2
    return 1
}

verify_uv_python_packages() {
    _verify_bluezero || return $?
    _verify_pygame_ce || return $?
    _verify_pybluez_dartsnut || return $?
    _verify_system_dbus || return $?
    _verify_system_gi
}

_uv_output_has_root_cache_error() {
    local output_file="$1"
    grep -q "Failed to write to the client cache" "${output_file}" ||
        grep -q "Bad message (os error 74)" "${output_file}"
}

_uv_output_has_filesystem_corruption() {
    local output_file="$1"
    grep -q "Structure needs cleaning" "${output_file}" ||
        grep -q "os error 117" "${output_file}"
}

_mark_update_repair_pending() {
    local marker
    local parent
    for marker in ${DARTSNUT_UPDATE_PENDING_MARKERS}; do
        parent="$(dirname "${marker}")"
        if [[ "${marker}" == /boot/* ]] && [ ! -d "${parent}" ]; then
            continue
        fi
        mkdir -p "${parent}" 2>/dev/null || continue
        printf 'pending\n' >"${marker}" 2>/dev/null || true
    done
}

_request_forcefsck() {
    local parent
    parent="$(dirname "${FORCEFSCK_PATH}")"
    mkdir -p "${parent}" 2>/dev/null || true
    if touch "${FORCEFSCK_PATH}" 2>/dev/null; then
        echo "uv cache clean hit filesystem corruption; requesting filesystem check on next boot (${FORCEFSCK_PATH})"
    else
        echo "uv cache clean hit filesystem corruption; unable to create ${FORCEFSCK_PATH}" >&2
    fi
}

_clean_uv_root_cache() {
    local clean_output
    local clean_status
    echo "uv cache appears corrupt; cleaning root uv cache and retrying..."
    clean_output="$(mktemp)"
    "${UV_BIN}" cache clean --force >"${clean_output}" 2>&1
    clean_status=$?
    cat "${clean_output}"
    if [ "${clean_status}" -ne 0 ] && _uv_output_has_filesystem_corruption "${clean_output}"; then
        _mark_update_repair_pending
        _request_forcefsck
    fi
    rm -f "${clean_output}"
    return "${clean_status}"
}

_run_uv_with_root_cache_repair() {
    local uv_output
    local status
    uv_output="$(mktemp)"

    "${UV_BIN}" "$@" >"${uv_output}" 2>&1
    status=$?
    cat "${uv_output}"
    if [ "${status}" -eq 0 ]; then
        rm -f "${uv_output}"
        return 0
    fi

    if _uv_output_has_root_cache_error "${uv_output}"; then
        local clean_status
        _clean_uv_root_cache || {
            clean_status=$?
            rm -f "${uv_output}"
            return "${clean_status}"
        }
        "${UV_BIN}" "$@" >"${uv_output}" 2>&1
        status=$?
        cat "${uv_output}"
    fi

    rm -f "${uv_output}"
    return "${status}"
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
            "${UV_BIN}" sync --inexact --refresh-package "${package}" >"${uv_output}" 2>&1
        else
            "${UV_BIN}" sync --inexact >"${uv_output}" 2>&1
        fi
        status=$?
        cat "${uv_output}"

        if [ "${status}" -eq 0 ]; then
            rm -f "${uv_output}"
            return 0
        fi

        if _uv_output_has_root_cache_error "${uv_output}"; then
            _clean_uv_root_cache || return $?
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
    local status

    resolve_uv_bin
    select_uv_default_index
    backup_uv_lock || return $?
    trap 'restore_uv_lock' RETURN INT TERM

    rm -rf "${REPO_DIR}/venv0" "${REPO_DIR}/.venv"
    sync_uv_project
    status=$?
    if [ "${status}" -eq 0 ]; then
        verify_uv_python_packages
        status=$?
    fi

    restore_uv_lock
    trap - RETURN INT TERM
    return "${status}"
}
