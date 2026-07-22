# Plateforme iBeacon V3

Cette version ajoute l'analyse multi-UUID.

Fonctions principales :

- capture iBeacon depuis nRF Connect Mobile ;
- détection des formats `4c000215`, `004c0215` et `0215` ;
- regroupement des paquets par UUID, Major, Minor et adresse ;
- statistiques globales ;
- statistiques séparées pour chaque UUID ;
- graphique RSSI avec une couleur différente pour chaque UUID ;
- graphique d'intervalles séparé par UUID ;
- histogrammes des durées par UUID ;
- export CSV et PNG.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_ibeacon_mobile_v3.zip
cd plateforme_ibeacon_mobile_v3
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```
