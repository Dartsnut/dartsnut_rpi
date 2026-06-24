#!/bin/bash

REPO_DIR="/home/rpi/dartsnut_rpi"
SERVICES_DIR="${REPO_DIR}/services"
UV_ENV_SCRIPT="${REPO_DIR}/scripts/uv_env.sh"
SYSTEM_PACKAGES_FILE="${REPO_DIR}/system-packages.txt"
INSTALL_PACKAGES_SCRIPT="${REPO_DIR}/scripts/install_system_packages.sh"

# shellcheck source=scripts/uv_env.sh
source "${UV_ENV_SCRIPT}"

SYSTEMD_UNITS_UPDATED=0

install_or_update_service_unit() {
    local unit_name="$1"
    local src="${SERVICES_DIR}/${unit_name}"
    local dst="/etc/systemd/system/${unit_name}"

    if [ ! -f "${src}" ]; then
        echo "Warning: ${unit_name} not found in ${SERVICES_DIR}; skipping."
        return 0
    fi

    if [ ! -f "${dst}" ] || ! cmp -s "${src}" "${dst}"; then
        echo "Installing/updating ${unit_name}"
        sudo install -m 0644 "${src}" "${dst}"
        SYSTEMD_UNITS_UPDATED=1
    else
        echo "${unit_name} already up to date, skipping."
    fi
}

# Install a file only when missing or different from src (dst is often root-owned; use sudo cmp).
install_if_changed() {
    local src="$1"
    local dst="$2"
    local mode="$3"
    local name="$4"

    if [ ! -f "${dst}" ] || ! sudo cmp -s "${src}" "${dst}"; then
        echo "Installing/updating ${name} at ${dst}"
        sudo install -m "${mode}" "${src}" "${dst}"
    else
        echo "${name} already up to date, skipping."
    fi
}
cleanup_legacy_splash_service() {
    echo "Cleaning up legacy splash service/binary"
    sudo systemctl disable dartsnut_splash.service >/dev/null 2>&1 || true
    sudo systemctl stop dartsnut_splash.service >/dev/null 2>&1 || true
    sudo rm -f /etc/systemd/system/dartsnut_splash.service
    sudo rm -f /etc/systemd/system/sysinit.target.wants/dartsnut_splash.service
    sudo rm -f /usr/local/bin/splash_matrix
}

echo "== Boot configuration (Bookworm) =="

if ! grep -q "^disable_splash=1" /boot/firmware/config.txt; then
    echo "disable_splash=1" | sudo tee -a /boot/firmware/config.txt > /dev/null
    echo "Added disable_splash=1 to /boot/firmware/config.txt"
else
    echo "disable_splash=1 already present in /boot/firmware/config.txt"
fi

if ! grep -q "^boot_delay=0" /boot/firmware/config.txt; then
    echo "boot_delay=0" | sudo tee -a /boot/firmware/config.txt > /dev/null
    echo "Added boot_delay=0 to /boot/firmware/config.txt"
else
    echo "boot_delay=0 already present in /boot/firmware/config.txt"
fi

if ! grep -qw "quiet" /boot/firmware/cmdline.txt; then
    sudo sed -i 's/$/ quiet/' /boot/firmware/cmdline.txt
    echo "Added quiet to /boot/firmware/cmdline.txt"
else
    echo "quiet already present in /boot/firmware/cmdline.txt"
fi

echo "== Services and boot assets =="

if [ -d "${SERVICES_DIR}" ]; then
    install_or_update_service_unit "dartsnut_matrix.service"
    install_or_update_service_unit "dartsnut_python.service"
    install_or_update_service_unit "dartsnut_mcp.service"

    SPLASH_DEST_PPM="/boot/logo.ppm"
    if [ ! -d "/boot" ] && [ -d "/boot/firmware" ]; then
        SPLASH_DEST_PPM="/boot/firmware/logo.ppm"
    fi

    if [ -f "${SERVICES_DIR}/logo.ppm" ]; then
        install_if_changed "${SERVICES_DIR}/logo.ppm" "${SPLASH_DEST_PPM}" 0644 logo.ppm
    else
        echo "Warning: logo.ppm not found in ${SERVICES_DIR}; skipping logo update."
    fi

    DEVICE_JSON_DEST="/boot/device.json"
    if [ ! -f "${DEVICE_JSON_DEST}" ]; then
        REPO_DEVICE_JSON="${REPO_DIR}/device.json"
        if [ -f "${REPO_DEVICE_JSON}" ]; then
            MODEL=$(grep -o '"model"[[:space:]]*:[[:space:]]*"[^"]*"' "${REPO_DEVICE_JSON}" | head -1 | sed 's/.*"model"[[:space:]]*:[[:space:]]*"//;s/"//')
            if [ -n "${MODEL}" ]; then
                echo "Creating partial device.json at ${DEVICE_JSON_DEST} with model=${MODEL}"
                echo "{\"model\": \"${MODEL}\", \"brightness\": \"100\"}" | sudo tee "${DEVICE_JSON_DEST}" > /dev/null
                if [ -f "${SPLASH_DEST_PPM}" ]; then
                    sudo chown --reference="${SPLASH_DEST_PPM}" "${DEVICE_JSON_DEST}"
                    sudo chmod --reference="${SPLASH_DEST_PPM}" "${DEVICE_JSON_DEST}"
                fi
            else
                echo "Warning: could not read model from ${REPO_DEVICE_JSON}; skipping /boot/device.json creation."
            fi
        else
            echo "Warning: ${REPO_DEVICE_JSON} not found; skipping /boot/device.json creation."
        fi
    else
        echo "device.json already present at ${DEVICE_JSON_DEST}"
    fi

    if [ "${SYSTEMD_UNITS_UPDATED}" -eq 1 ]; then
        sudo systemctl daemon-reload
    fi

    cleanup_legacy_splash_service
    sudo systemctl daemon-reload
    sudo systemctl enable dartsnut_matrix.service
    sudo systemctl enable dartsnut_mcp.service
    if ! sudo systemctl is-active --quiet dartsnut_mcp.service; then
        sudo systemctl start dartsnut_mcp.service
    fi
else
    echo "Warning: services directory not found at ${SERVICES_DIR}; skipping boot asset update."
fi

echo "== Python deps refresh (uv) =="
"${INSTALL_PACKAGES_SCRIPT}" "${SYSTEM_PACKAGES_FILE}"
refresh_uv_project

echo "== Network tuning (Supabase / Wi-Fi stability) =="

DARTSNUT_SYSCONF_SRC="${REPO_DIR}/services/99-dartsnut-tcp.conf"
DARTSNUT_SYSCONF_DST="/etc/sysctl.d/99-dartsnut-tcp.conf"
if [ -f "${DARTSNUT_SYSCONF_SRC}" ]; then
    install_if_changed "${DARTSNUT_SYSCONF_SRC}" "${DARTSNUT_SYSCONF_DST}" 0644 "99-dartsnut-tcp.conf"
    sudo sysctl -p "${DARTSNUT_SYSCONF_DST}" >/dev/null 2>&1 || true
else
    echo "Warning: ${DARTSNUT_SYSCONF_SRC} not found; skipping TCP MTU probing sysctl."
fi

if command -v nmcli >/dev/null 2>&1; then
    WIFI_CON_NAME="$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2 == "802-11-wireless" { print $1; exit }')"
    if [ -n "${WIFI_CON_NAME}" ]; then
        echo "Disabling Wi-Fi power save on connection ${WIFI_CON_NAME}"
        sudo nmcli connection modify "${WIFI_CON_NAME}" 802-11-wireless.powersave 2
    else
        echo "No active Wi-Fi connection profile found; skipping nmcli power-save change."
    fi
fi

if command -v iwconfig >/dev/null 2>&1 && iwconfig wlan0 >/dev/null 2>&1; then
    echo "Disabling wlan0 Wi-Fi power management (iwconfig)"
    sudo iwconfig wlan0 power off || true
fi

echo "== Cron auto-update =="

CRON_SCHEDULE="0 3 * * *"  # 3am every day
UPDATE_SCRIPT="${REPO_DIR}/check_and_update.py"
CRON_ENTRY="${CRON_SCHEDULE} ${UV_BIN} run --directory ${REPO_DIR} ${UPDATE_SCRIPT} >> /var/log/dartsnut_update.log 2>&1"

EXISTING_CRON=$(sudo crontab -l 2>/dev/null || echo "")

if echo "$EXISTING_CRON" | grep -q "check_and_update.py"; then
    echo "Update cron job already exists. Updating it..."
    NEW_CRON=$(echo "$EXISTING_CRON" | grep -v "check_and_update.py")
    echo "$NEW_CRON" | grep -v "^$" > /tmp/new_crontab.txt 2>/dev/null || true
    echo "$CRON_ENTRY" >> /tmp/new_crontab.txt
    sudo crontab /tmp/new_crontab.txt
    rm -f /tmp/new_crontab.txt
    echo "Updated cron job for automatic git updates"
else
    echo "Adding cron job for automatic git updates..."
    if [ -n "$EXISTING_CRON" ]; then
        echo "$EXISTING_CRON" > /tmp/new_crontab.txt
    else
        touch /tmp/new_crontab.txt
    fi
    echo "$CRON_ENTRY" >> /tmp/new_crontab.txt
    sudo crontab /tmp/new_crontab.txt
    rm -f /tmp/new_crontab.txt
    echo "Added cron job for automatic git updates"
fi

echo "== Restart =="

sudo systemctl restart dartsnut_python.service
echo "Restarted dartsnut_python.service"
