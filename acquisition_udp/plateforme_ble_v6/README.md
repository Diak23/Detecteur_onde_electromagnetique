# Plateforme BLE V6

Application Raspberry Pi complète pour :

- émettre un iBeacon par D-Bus avec `org.bluez.LEAdvertisingManager1` ;
- capturer les trames avec le nRF Sniffer et `tshark` ;
- décoder UUID, Major, Minor et Tx Power ;
- regrouper les paquets des canaux 37/38/39 en événements ;
- mesurer RSSI, durée observée et intervalle ;
- calibrer le RSSI à 1 mètre ;
- exporter CSV, JSON, graphes PNG et rapport PDF.

## 1. Remettre le contrôleur sous le contrôle de BlueZ

Tu as activé l’advertising directement avec `btmgmt`. Avant de lancer l’application, exécute :

```bash
sudo btmgmt advertising off
sudo systemctl restart bluetooth
```

Puis vérifie :

```bash
busctl get-property org.bluez /org/bluez/hci0 \
  org.bluez.LEAdvertisingManager1 ActiveInstances
```

Avant le lancement, le résultat normal est :

```text
y 0
```

## 2. Installation

Copie ou décompresse le projet dans `~/acquisition_udp`, puis :

```bash
cd ~/acquisition_udp/plateforme_ble_v6
chmod +x install.sh diagnostic.sh
./install.sh
```

Les modules `dbus` et `gi` sont fournis par APT. Crée donc le venv avec accès aux modules système :

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
```

Teste les imports :

```bash
python3 -c "import dbus; from gi.repository import GLib; print('D-Bus OK')"
```

## 3. Lancement

```bash
sudo systemctl start bluetooth
source venv/bin/activate
python3 main.py
```

Ne lance pas l’interface avec `sudo`.

## 4. Émission iBeacon

Le programme construit automatiquement :

```text
Company ID Apple : 0x004C
Type iBeacon     : 0x02
Longueur         : 0x15
UUID             : 16 octets
Major            : 2 octets big-endian
Minor            : 2 octets big-endian
Tx Power         : 1 octet signé
```

Après **Démarrer**, clique sur **Vérifier ActiveInstances**. Une émission correctement enregistrée doit donner :

```text
y 1
```

## 5. Capture nRF

Le champ d’interface doit correspondre exactement à une ligne de :

```bash
tshark -D
```

Dans ton installation, la valeur observée était :

```text
/dev/ttyUSB0-4.4
```

Si UUID/Major/Minor restent vides alors que les trames arrivent, recherche le nom du champ Manufacturer Data de ta version de Wireshark :

```bash
tshark -G fields | grep -i -E "manufacturer|eir_ad.entry.data"
```

Puis remplace, dans `main.py`, le champ :

```python
"btcommon.eir_ad.entry.data"
```

## 6. Calibration RSSI

1. Place le beacon à 1 mètre du sniffer.
2. Lance une capture de plusieurs secondes.
3. Saisis le RSSI de référence.
4. Clique sur **Calculer la calibration**.

Formules :

```text
offset = RSSI_référence − moyenne_RSSI_brut
RSSI_calibré = RSSI_brut + offset
```

## 7. Export automatique

À l’arrêt d’une capture, le programme crée automatiquement :

```text
acquisitions_ble/acquisition_YYYYMMDD_HHMMSS/
├── trames_ble.csv
├── evenements_advertising.csv
├── statistiques.csv
├── configuration.json
├── calibration_rssi.json
├── rapport_ble.pdf
└── graphes/
    ├── 01_rssi_temps.png
    ├── 02_histogramme_rssi.png
    ├── 03_intervalles.png
    ├── 04_durees.png
    └── 05_canaux.png
```

## 8. Précision scientifique

La « durée d’un événement » calculée ici correspond au temps observé entre la première et la dernière trame capturée appartenant au même événement. Elle ne représente pas directement le temps radio exact d’un PDU unique.

La fenêtre de regroupement est réglée par défaut à 12 ms et peut être modifiée dans l’interface.

## 9. Intervalle d’émission

L’API standard `LEAdvertisement1` ne garantit pas le réglage direct de l’intervalle Legacy sur toutes les versions de BlueZ. Le programme mesure donc l’intervalle réel avec le nRF Sniffer au lieu d’afficher une consigne potentiellement non appliquée.

## 10. Diagnostic

```bash
./diagnostic.sh
sudo journalctl -u bluetooth --since "5 minutes ago" --no-pager
```

En cas de nouvel échec :

```bash
sudo btmgmt advertising off
sudo systemctl restart bluetooth
```
