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

# 3. Create blacklist-bcm2835.conf if it doesn't exist
if [ ! -f /etc/modprobe.d/blacklist-bcm2835.conf ]; then
    echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-bcm2835.conf > /dev/null
    echo "Created blacklist-bcm2835.conf"
else
    echo "blacklist-bcm2835.conf already exists"
fi

# 4. Create 99-hid.rules if it doesn't exist, then reload and trigger udev
if [ ! -f /etc/udev/rules.d/99-hid.rules ]; then
    echo 'SUBSYSTEM=="hidraw",MODE="0666"' | sudo tee /etc/udev/rules.d/99-hid.rules > /dev/null
    echo "Created 99-hid.rules"
else
    echo "99-hid.rules already exists"
fi
sudo udevadm control --reload-rules
sudo udevadm trigger

# 5. Create a python3 venv
sudo python3 -m venv venv0

# 6. Install dependencies
sudo apt-get update
sudo apt-get install libcairo2-dev python3-cairo -y
sudo apt-get install python3-dev -y
sudo apt-get install libgirepository1.0-dev gir1.2-glib-2.0 -y
sudo apt-get install libdbus-1-dev -y
sudo apt-get install libbluetooth-dev -y
sudo apt-get install libgl1 -y
sudo apt-get install git -y

# 7. Install the python modules
sudo venv0/bin/pip install -r requirement.txt

# 8. Set Swap Memory to 0
sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=0/' /etc/dphys-swapfile

# If CONF_SWAPSIZE is not present, add it
if ! grep -q "^CONF_SWAPSIZE=" /etc/dphys-swapfile; then
  echo "CONF_SWAPSIZE=0" | sudo tee -a /etc/dphys-swapfile
fi

# 9. Create the services
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

# 10. Edit /etc/bluetooth/main.conf: change "# ReverseServiceDiscovery = true" to "ReverseServiceDiscovery = false"
sudo sed -i 's/^# ReverseServiceDiscovery = true/ReverseServiceDiscovery = false/' /etc/bluetooth/main.conf
echo "Updated ReverseServiceDiscovery in /etc/bluetooth/main.conf"
