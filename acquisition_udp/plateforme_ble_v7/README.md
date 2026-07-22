# Plateforme BLE V7

## Architecture
- `emission_dbus.py` : émetteur iBeacon minimal autonome avec boucle GLib.
- `test_emission.py` : validation de l'émetteur avant l'interface.
- `interface.py` : interface Tkinter qui lance l'émetteur validé comme sous-processus.
- `capture_nrf.py`, `analysis_ble.py`, `calibration.py`, `exports.py`, `graphs.py` : modules indépendants.

## Installation
```bash
cd ~/acquisition_udp
unzip plateforme_ble_v7.zip
cd plateforme_ble_v7
chmod +x *.sh emission_dbus.py test_emission.py main.py
./install.sh
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
python3 -c "import dbus; from gi.repository import GLib; print('D-Bus OK')"
```

## Préparation
```bash
sudo btmgmt advertising off
sudo systemctl restart bluetooth
sleep 3
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 ActiveInstances
```
Résultat initial attendu : `y 0`.

## Test minimal obligatoire
```bash
python3 test_emission.py
```
Résultats attendus :
```text
ActiveInstances avant : 0
REGISTERED {...}
ActiveInstances pendant : 1
```
Vérifie alors dans nRF Connect Mobile que l'iBeacon est reçu. Après Entrée, `ActiveInstances après : 0`.

## Plateforme complète
```bash
python3 main.py
```

`ActiveInstances = 1` valide l'enregistrement BlueZ. La réception par nRF Connect ou le nRF Sniffer valide réellement la diffusion radio.
