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

if not isinstance(data, dict) or not str(data.get("model") or "").strip():
    raise SystemExit(1)
PY
}

copy_device_json() {
    local src="$1"
    local dst="$2"
    local label="$3"
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

BOOT_DEVICE_JSON_VALID=0
WORK_DEVICE_JSON_VALID=0
device_json_is_valid "${BOOT_DEVICE_JSON}" && BOOT_DEVICE_JSON_VALID=1
device_json_is_valid "${WORK_DEVICE_JSON}" && WORK_DEVICE_JSON_VALID=1

if [ "${BOOT_DEVICE_JSON_VALID}" -eq 1 ] && [ "${WORK_DEVICE_JSON_VALID}" -eq 1 ]; then
    echo "device.json present at ${BOOT_DEVICE_JSON} and ${WORK_DEVICE_JSON}"
elif [ "${WORK_DEVICE_JSON_VALID}" -eq 1 ]; then
    echo "Restoring missing boot device.json from ${WORK_DEVICE_JSON}"
    copy_device_json "${WORK_DEVICE_JSON}" "${BOOT_DEVICE_JSON}" "boot device.json"
elif [ "${BOOT_DEVICE_JSON_VALID}" -eq 1 ]; then
    echo "Restoring missing runtime device.json from ${BOOT_DEVICE_JSON}"
    copy_device_json "${BOOT_DEVICE_JSON}" "${WORK_DEVICE_JSON}" "runtime device.json"
else
    echo "Warning: device.json missing or invalid in both locations: ${BOOT_DEVICE_JSON}, ${WORK_DEVICE_JSON}"
fi
