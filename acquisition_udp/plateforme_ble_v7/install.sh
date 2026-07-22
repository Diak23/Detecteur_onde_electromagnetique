#!/usr/bin/env bash
set -euo pipefail
sudo apt update
sudo apt install -y bluez bluetooth python3-dbus python3-gi python3-tk tshark wireshark-common
echo "python3 -m venv --system-site-packages venv"
echo "source venv/bin/activate"
echo "pip install -r requirements.txt"
