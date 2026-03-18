#!/bin/bash

# Step 7: Install the python modules
sudo venv0/bin/pip install --upgrade pip
sudo venv0/bin/pip install --upgrade -r requirement.txt

# Ensure boot configuration tweaks are present (Bookworm paths)
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

# Refresh PixelDarts early-boot splash from local boot_splash
BOOT_SPLASH_DIR="/home/rpi/dartsnut_rpi/boot_splash"

if [ -d "${BOOT_SPLASH_DIR}" ]; then
    if [ -f "${BOOT_SPLASH_DIR}/splash_matrix" ]; then
        echo "Updating splash_matrix at /usr/local/bin/splash_matrix"
        sudo install -m 0755 "${BOOT_SPLASH_DIR}/splash_matrix" /usr/local/bin/splash_matrix
    else
        echo "Warning: splash_matrix not found in ${BOOT_SPLASH_DIR}; skipping binary update."
    fi

    SPLASH_DEST_PPM="/boot/pixeldarts_logo.ppm"
    if [ ! -d "/boot" ] && [ -d "/boot/firmware" ]; then
        SPLASH_DEST_PPM="/boot/firmware/pixeldarts_logo.ppm"
    fi

    if [ -f "${BOOT_SPLASH_DIR}/pixeldarts_logo.ppm" ]; then
        echo "Updating pixeldarts_logo.ppm at ${SPLASH_DEST_PPM}"
        sudo install -m 0644 "${BOOT_SPLASH_DIR}/pixeldarts_logo.ppm" "${SPLASH_DEST_PPM}"
    else
        echo "Warning: pixeldarts_logo.ppm not found in ${BOOT_SPLASH_DIR}; skipping logo update."
    fi

    if [ -f "${BOOT_SPLASH_DIR}/pixeldarts-splash.service" ]; then
        echo "Updating pixeldarts-splash.service"
        sudo install -m 0644 "${BOOT_SPLASH_DIR}/pixeldarts-splash.service" /etc/systemd/system/pixeldarts-splash.service
        sudo systemctl daemon-reload
    else
        echo "Warning: pixeldarts-splash.service not found in ${BOOT_SPLASH_DIR}; skipping service update."
    fi
else
    echo "Warning: boot_splash directory not found at ${BOOT_SPLASH_DIR}; skipping early-boot splash update."
fi

# Step 12: Setup cron job for automatic git updates
CRON_SCHEDULE="0 3 * * *"  # 3am every day
UPDATE_SCRIPT="/home/rpi/dartsnut_rpi/check_and_update.py"
PYTHON_INTERPRETER="/home/rpi/dartsnut_rpi/venv0/bin/python"
CRON_ENTRY="${CRON_SCHEDULE} ${PYTHON_INTERPRETER} ${UPDATE_SCRIPT} >> /var/log/dartsnut_update.log 2>&1"

# Get existing cron jobs (or empty if none exist)
EXISTING_CRON=$(sudo crontab -l 2>/dev/null || echo "")

# Check if a cron entry for check_and_update.py already exists
if echo "$EXISTING_CRON" | grep -q "check_and_update.py"; then
    echo "Update cron job already exists. Updating it..."
    # Remove existing entry for check_and_update.py and add new one
    NEW_CRON=$(echo "$EXISTING_CRON" | grep -v "check_and_update.py")
    # Add new entry
    echo "$NEW_CRON" | grep -v "^$" > /tmp/new_crontab.txt 2>/dev/null || true
    echo "$CRON_ENTRY" >> /tmp/new_crontab.txt
    sudo crontab /tmp/new_crontab.txt
    rm -f /tmp/new_crontab.txt
    echo "Updated cron job for automatic git updates"
else
    echo "Adding cron job for automatic git updates..."
    # Add new entry while preserving existing ones
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

# Restart dartsnut_python.service
sudo systemctl restart dartsnut_python.service
echo "Restarted dartsnut_python.service"
