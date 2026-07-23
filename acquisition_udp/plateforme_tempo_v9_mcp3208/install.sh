#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install -y \
    python3-tk \
    python3-venv \
    python3-spidev \
    raspi-config

python3 -m venv --system-site-packages venv
source venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

chmod +x main.py diagnostic.sh

echo
echo "Activez SPI si nécessaire :"
echo "sudo raspi-config"
echo "Interface Options > SPI > Enable"
echo
echo "Lancement :"
echo "source venv/bin/activate"
echo "python3 main.py"
