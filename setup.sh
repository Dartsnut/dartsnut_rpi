#!/bin/bash

# 1. Append isolcpus=3 to /boot/firmware/cmdline.txt if not already present
if ! grep -qw "isolcpus=3" /boot/firmware/cmdline.txt; then
    echo -n " isolcpus=3" | sudo tee -a /boot/firmware/cmdline.txt > /dev/null
    echo "Added isolcpus=3 to cmdline.txt"
else
    echo "isolcpus=3 already present in cmdline.txt"
fi

# 2. Change dtparam=audio=on to dtparam=audio=off and dtoverlay=vc4-kms-v3d to dtoverlay=vc4-kms-v3d,noaudio in /boot/firmware/config.txt
sudo sed -i 's/dtparam=audio=on/dtparam=audio=off/g' /boot/firmware/config.txt
sudo sed -i '/dtoverlay=vc4-kms-v3d$/ s/$/,noaudio/' /boot/firmware/config.txt

# 3. Apply additional boot configuration tweaks for early splash (Bookworm paths)
#    - Ensure disable_splash=1 and boot_delay=0 are present in /boot/firmware/config.txt
#    - Ensure 'quiet' is present in /boot/firmware/cmdline.txt
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

# 4. Install PixelDarts early-boot splash from local boot_splash
BOOT_SPLASH_DIR="/home/rpi/dartsnut_rpi/boot_splash"

if [ -d "${BOOT_SPLASH_DIR}" ]; then
    if [ -f "${BOOT_SPLASH_DIR}/splash_matrix" ]; then
        echo "Installing splash_matrix to /usr/local/bin/splash_matrix"
        sudo install -m 0755 "${BOOT_SPLASH_DIR}/splash_matrix" /usr/local/bin/splash_matrix
    else
        echo "Warning: splash_matrix not found in ${BOOT_SPLASH_DIR}; skipping binary install."
    fi

    SPLASH_DEST_PPM="/boot/pixeldarts_logo.ppm"
    if [ ! -d "/boot" ] && [ -d "/boot/firmware" ]; then
        SPLASH_DEST_PPM="/boot/firmware/pixeldarts_logo.ppm"
    fi

    if [ -f "${BOOT_SPLASH_DIR}/pixeldarts_logo.ppm" ]; then
        echo "Copying pixeldarts_logo.ppm to ${SPLASH_DEST_PPM}"
        sudo install -m 0644 "${BOOT_SPLASH_DIR}/pixeldarts_logo.ppm" "${SPLASH_DEST_PPM}"
    else
        echo "Warning: pixeldarts_logo.ppm not found in ${BOOT_SPLASH_DIR}; skipping logo copy."
    fi

    if [ -f "${BOOT_SPLASH_DIR}/pixeldarts-splash.service" ]; then
        echo "Installing pixeldarts-splash.service"
        sudo install -m 0644 "${BOOT_SPLASH_DIR}/pixeldarts-splash.service" /etc/systemd/system/pixeldarts-splash.service
        sudo systemctl daemon-reload
        sudo systemctl enable pixeldarts-splash.service
    else
        echo "Warning: pixeldarts-splash.service not found in ${BOOT_SPLASH_DIR}; skipping service install."
    fi
else
    echo "Warning: boot_splash directory not found at ${BOOT_SPLASH_DIR}; skipping early-boot splash setup."
fi

# 5. Create blacklist-bcm2835.conf if it doesn't exist
if [ ! -f /etc/modprobe.d/blacklist-bcm2835.conf ]; then
    echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-bcm2835.conf > /dev/null
    echo "Created blacklist-bcm2835.conf"
else
    echo "blacklist-bcm2835.conf already exists"
fi

# 6. Create 99-hid.rules if it doesn't exist, then reload and trigger udev
if [ ! -f /etc/udev/rules.d/99-hid.rules ]; then
    echo 'SUBSYSTEM=="hidraw",MODE="0666"' | sudo tee /etc/udev/rules.d/99-hid.rules > /dev/null
    echo "Created 99-hid.rules"
else
    echo "99-hid.rules already exists"
fi
sudo udevadm control --reload-rules
sudo udevadm trigger

# 7. Create a python3 venv
sudo python3 -m venv venv0

# 8. Install dependencies
sudo apt-get update
sudo apt-get install libcairo2-dev python3-cairo -y
sudo apt-get install python3-dev -y
sudo apt-get install libgirepository1.0-dev gir1.2-glib-2.0 -y
sudo apt-get install libdbus-1-dev -y
sudo apt-get install libbluetooth-dev -y
sudo apt-get install libgl1 -y
sudo apt-get install git -y
sudo apt-get install xvfb -y
sudo apt-get install libsdl2-dev -y
sudo apt-get install libgpiod-dev gpiod -y
sudo apt-get install -y cmake ninja-build libssl-dev libcurl4-openssl-dev zlib1g-dev
sudo apt-get install -y libprotobuf-dev protobuf-compiler
sudo apt-get install -y libgoogle-cloud-firestore-dev libgoogle-cloud-cpp-dev || true

# 9. Install the python modules
sudo venv0/bin/pip install --upgrade pip
sudo venv0/bin/pip install --upgrade -r requirement.txt

# 10. Set Swap Memory to 0
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=0/' /etc/dphys-swapfile

# If CONF_SWAPSIZE is not present, add it
if ! grep -q "^CONF_SWAPSIZE=" /etc/dphys-swapfile; then
  echo "CONF_SWAPSIZE=0" | sudo tee -a /etc/dphys-swapfile
fi

# 11. Create the services
if [ ! -f /etc/systemd/system/dartsnut_matrix.service ]; then
    sudo tee /etc/systemd/system/dartsnut_matrix.service > /dev/null <<EOL
[Unit]
Description=Dartsnut RGB Matrix Service

[Service]
Type=simple
User=root
WorkingDirectory=/home/rpi/dartsnut_rpi
ExecStart=/home/rpi/dartsnut_rpi/DartsnutRGBMatrix
Restart=always

[Install]
WantedBy=multi-user.target
EOL
    echo "Created dartsnut_matrix.service"
else
    echo "dartsnut_matrix.service already exists, skipping."
fi

# Create dartsnut_python.service if it does not exist
if [ ! -f /etc/systemd/system/dartsnut_python.service ]; then
    sudo tee /etc/systemd/system/dartsnut_python.service > /dev/null <<EOL
[Unit]
Description=Dartsnut Python Service
After=bluetooth.target network.target dartsnut_matrix.service
Requires=bluetooth.target network.target dartsnut_matrix.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/rpi/dartsnut_rpi
ExecStart=/home/rpi/dartsnut_rpi/venv0/bin/python /home/rpi/dartsnut_rpi/main.py
Restart=always

[Install]
WantedBy=multi-user.target
EOL
    echo "Created dartsnut_python.service"
else
    echo "dartsnut_python.service already exists, skipping."
fi

# Reload systemd to recognize new services
sudo systemctl daemon-reload
sudo systemctl enable dartsnut_matrix.service
sudo systemctl enable dartsnut_python.service

echo "Service file creation steps complete."

# 12. Edit /etc/bluetooth/main.conf: change "#ReverseServiceDiscovery = true" to "ReverseServiceDiscovery = false"
sudo sed -i 's/^#ReverseServiceDiscovery = true/ReverseServiceDiscovery = false/' /etc/bluetooth/main.conf
echo "Updated ReverseServiceDiscovery in /etc/bluetooth/main.conf"

# 13. Add safe directory for git
sudo git config --global --add safe.directory /home/rpi/dartsnut_rpi

# 14. Setup cron job for automatic git updates
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
