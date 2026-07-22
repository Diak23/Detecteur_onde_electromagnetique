#!/usr/bin/env bash
set -e
sudo apt update
sudo apt install -y python3-tk python3-venv tshark wireshark-common
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
chmod +x main.py diagnostic.sh
echo "Installation terminée."
echo "Lancement : source venv/bin/activate && python3 main.py"
