#!/bin/bash

REPO_DIR="${REPO_DIR:-/home/rpi/dartsnut_rpi}"
BOOT_DEVICE_JSON="${BOOT_DEVICE_JSON:-/boot/device.json}"
WORK_DEVICE_JSON="${WORK_DEVICE_JSON:-${REPO_DIR}/device.json}"
INSTALL_CMD="${INSTALL_CMD:-sudo install}"

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
        return 0
    fi

    ${INSTALL_CMD} -m 0644 "${src}" "${dst}"
}

if [ -f "${BOOT_DEVICE_JSON}" ] && [ -f "${WORK_DEVICE_JSON}" ]; then
    echo "device.json present at ${BOOT_DEVICE_JSON} and ${WORK_DEVICE_JSON}"
elif [ -f "${WORK_DEVICE_JSON}" ]; then
    echo "Restoring missing boot device.json from ${WORK_DEVICE_JSON}"
    copy_device_json "${WORK_DEVICE_JSON}" "${BOOT_DEVICE_JSON}" "boot device.json"
elif [ -f "${BOOT_DEVICE_JSON}" ]; then
    echo "Restoring missing runtime device.json from ${BOOT_DEVICE_JSON}"
    copy_device_json "${BOOT_DEVICE_JSON}" "${WORK_DEVICE_JSON}" "runtime device.json"
else
    echo "Warning: device.json missing in both locations: ${BOOT_DEVICE_JSON}, ${WORK_DEVICE_JSON}"
fi
