#!/usr/bin/env bash
set -euo pipefail
sudo apt update
sudo apt install -y bluez bluetooth python3-dbus python3-gi python3-tk tshark wireshark-common
printf '\nCrée le venv ainsi :\n'
printf 'python3 -m venv --system-site-packages venv\n'
printf 'source venv/bin/activate\n'
printf 'pip install -r requirements.txt\n'
printf '\nPuis ajoute éventuellement ton utilisateur aux groupes :\n'
printf 'sudo usermod -aG bluetooth,wireshark,dialout $USER\n'
