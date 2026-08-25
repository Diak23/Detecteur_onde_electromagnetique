# Plateforme TEMPO V10 — instrument multimode

La V10 sépare clairement les données simulées des mesures physiques.

## Sources disponibles

- **Simulation RF** : test de l'interface sans matériel ;
- **MCP3208 réel** : lecture de la chaîne antenne–filtre–LNA–détecteur ;
- **BLE** : iBeacon, AirPods et autres advertisements via nRF Sniffer ;
- **Wi-Fi** : RSSI de la liaison active via `iw`.

Chaque ligne exportée contient un champ `simulated` qui indique clairement
si la valeur est simulée ou réelle.

## Fonctions

- acquisition simultanée de plusieurs sources ;
- bilan de puissance configurable pour 868 MHz et 2,45 GHz ;
- lecture MCP3208 ;
- détection BLE ;
- lecture RSSI Wi-Fi ;
- puissance reçue ;
- énergie RF reçue estimée ;
- alertes vert/orange/rouge ;
- contrôle par durée ou nombre de mesures ;
- CSV et graphiques PNG.

## Graphiques multi-sources

Les graphiques sont organisés en sous-graphes afin de rendre lisible une
acquisition comportant plusieurs sources :

- un sous-graphe par source et par bande RF ;
- plusieurs courbes seulement à l'intérieur du panneau BLE, une par appareil ;
- légende BLE placée sous les sous-graphes, hors de la zone de tracé ;
- identification individuelle des six appareils BLE les plus observés ; les
  autres restent tracés en gris sous une entrée de légende commune ;
- fond bleu et trait continu pour les données réelles ;
- fond orange et trait pointillé pour les simulations ;
- graphe RSSI réservé aux récepteurs numériques Wi-Fi, BLE et LoRa ;
- graphe de puissance réservé aux voies RF `MCP3208` et `Simulation RF` ;
- énergie cumulée affichée séparément pour éviter d'additionner visuellement
  des indicateurs issus de méthodes de mesure différentes.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_tempo_v10_multimode.zip
cd plateforme_tempo_v10_multimode
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```

## Règle importante

- Pour un essai sans matériel : cochez uniquement **Simulation RF**.
- Pour une mesure physique : décochez **Simulation RF**, puis activez
  MCP3208, BLE ou Wi-Fi selon le matériel disponible.
- Ne présentez jamais une donnée marquée `simulated=True` comme une mesure
  expérimentale.


## LoRa 868 MHz

La plateforme propose une simulation LoRa et un module réel par port série. Le module réel doit envoyer une ligne JSON par paquet :

```json
{"frequency_mhz":868.1,"rssi_dbm":-83,"snr_db":7.5,"sf":7,"bw_khz":125,"cr":"4/5","length":12,"crc_ok":true,"payload_hex":"48656c6c6f"}
```

La chaîne analogique 868 MHz détecte l'énergie RF mais ne décode pas les paquets LoRa.
