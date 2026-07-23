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
