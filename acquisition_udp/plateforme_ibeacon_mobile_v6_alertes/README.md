# Plateforme iBeacon Mobile V6 — alertes

## Nouveautés

- nouvel onglet **Affichage / Alertes** ;
- voyant général vert, orange ou rouge ;
- niveau distinct pour chaque appareil logique ;
- choix de la grandeur surveillée :
  - RSSI ;
  - puissance reçue ;
  - énergie de la trame ;
  - énergie cumulée ;
  - débit de trames ;
- seuils Vert/Orange et Orange/Rouge configurables ;
- historique des transitions d'alerte ;
- export des durées passées dans chaque niveau ;
- le chronomètre démarre seulement à la première trame iBeacon ;
- avertissement dans le journal après 10 secondes sans trame.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_ibeacon_mobile_v6_alertes.zip
cd plateforme_ibeacon_mobile_v6_alertes
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```

## Fichiers supplémentaires

- `historique_alertes.csv`
- `durees_niveaux_alerte.csv`

Les seuils sont configurables et servent à une classification expérimentale.
Ils ne constituent pas des limites réglementaires d'exposition.
