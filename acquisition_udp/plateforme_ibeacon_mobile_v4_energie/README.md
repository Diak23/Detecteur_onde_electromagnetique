# Plateforme iBeacon V4 — mesure d'énergie RF reçue

Cette version calcule :

- puissance reçue à partir du RSSI ;
- durée radio estimée de chaque trame ;
- énergie reçue par trame ;
- énergie reçue par événement ;
- énergie cumulée par UUID ;
- graphes avec une couleur distincte par UUID ;
- export CSV et PNG.

Formules :

```text
P(W) = 10^((RSSI_dBm - 30) / 10)
E(J) = P(W) × durée(s)
```

Pour LE 1M :

```text
durée_us = (longueur_tshark + 10) × 8
```

La valeur obtenue est une estimation de l'énergie RF reçue au niveau du sniffer. Elle ne représente pas la consommation électrique du téléphone ou du beacon.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_ibeacon_mobile_v4_energie.zip
cd plateforme_ibeacon_mobile_v4_energie
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```
