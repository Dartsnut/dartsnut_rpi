#!/bin/bash

REPO_DIR="${REPO_DIR:-/home/rpi/dartsnut_rpi}"
BOOT_DEVICE_JSON="${BOOT_DEVICE_JSON:-/boot/device.json}"
WORK_DEVICE_JSON="${WORK_DEVICE_JSON:-${REPO_DIR}/device.json}"
INSTALL_CMD="${INSTALL_CMD:-sudo install}"
read -r -a INSTALL_CMD_PARTS <<< "${INSTALL_CMD}"

device_json_is_valid() {
    local path="$1"
    [ -f "${path}" ] || return 1
    python3 - "${path}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

if not isinstance(data, dict) or not str(data.get("serial") or "").strip():
    raise SystemExit(1)
PY
}

copy_device_json() {
    local src="$1"
    local dst="$2"
    local label="$3"
    if python3 - "${dst}" <<'PY'
import os
import sys

raise SystemExit(
    0 if os.path.realpath(sys.argv[1]) == os.path.realpath("/boot/serial.txt") else 1
)
PY
    then
        echo "Warning: refusing to overwrite /boot/serial.txt"
        return 1
    fi
    local parent
    parent="$(dirname "${dst}")"

    if [ ! -d "${parent}" ]; then
        mkdir -p "${parent}" 2>/dev/null || true
    fi
    if [ ! -d "${parent}" ]; then
        echo "Warning: cannot restore ${label}; parent missing: ${parent}"
        return 1
    fi

    "${INSTALL_CMD_PARTS[@]}" -m 0644 "${src}" "${dst}"
}

reconcile_work_device_identity() {
    local python_bin="${REPO_DIR}/.venv/bin/python"
    [ -f "${REPO_DIR}/runtime/device_json_identity.py" ] || return 0
    [ -f "${WORK_DEVICE_JSON}" ] || return 0
    [ -x "${python_bin}" ] || python_bin="python3"

    if ! PYTHONPATH="${REPO_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
        "${python_bin}" - "${WORK_DEVICE_JSON}" <<'PY'
import sys

from runtime.device_json_identity import reconcile_device_json_identity

reconcile_device_json_identity(sys.argv[1])
PY
    then
        echo "Warning: could not reconcile local device identity in ${WORK_DEVICE_JSON}"
    fi
}

BOOT_DEVICE_JSON_VALID=0
WORK_DEVICE_JSON_VALID=0
device_json_is_valid "${BOOT_DEVICE_JSON}" && BOOT_DEVICE_JSON_VALID=1
device_json_is_valid "${WORK_DEVICE_JSON}" && WORK_DEVICE_JSON_VALID=1

if [ "${BOOT_DEVICE_JSON_VALID}" -eq 1 ] && [ "${WORK_DEVICE_JSON_VALID}" -eq 1 ]; then
    echo "device.json present at ${BOOT_DEVICE_JSON} and ${WORK_DEVICE_JSON}"
elif [ "${WORK_DEVICE_JSON_VALID}" -eq 1 ]; then
    echo "Restoring missing boot device.json from ${WORK_DEVICE_JSON}"
    copy_device_json "${WORK_DEVICE_JSON}" "${BOOT_DEVICE_JSON}" "boot device.json" || exit $?
elif [ "${BOOT_DEVICE_JSON_VALID}" -eq 1 ]; then
    echo "Restoring missing runtime device.json from ${BOOT_DEVICE_JSON}"
    copy_device_json "${BOOT_DEVICE_JSON}" "${WORK_DEVICE_JSON}" "runtime device.json" || exit $?
else
    echo "Warning: device.json missing or invalid in both locations: ${BOOT_DEVICE_JSON}, ${WORK_DEVICE_JSON}"
fi

reconcile_work_device_identity
