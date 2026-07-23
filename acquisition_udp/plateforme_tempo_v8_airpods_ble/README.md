# Plateforme TEMPO V8 — AirPods et appareils BLE

## Fonctions ajoutées

- conservation de toutes les fonctions TEMPO V7 ;
- détection des iBeacons ;
- détection des données constructeur Apple ;
- détection des AirPods lorsque le nom local est visible ;
- classification « AirPods / accessoire audio Apple probable » lorsque
  l'identification repose sur une signature publicitaire Apple ;
- affichage du fabricant, du type détecté et du niveau de confiance ;
- nouvel onglet `Appareils BLE détectés` ;
- export `appareils_ble_detectes.csv` ;
- longueur, type de PDU, canal, RSSI, puissance, énergie et indice TEMPO.

## Important

Les AirPods utilisent des formats propriétaires et peuvent employer des
adresses aléatoires. Une trame Apple sans nom local ne permet pas toujours de
distinguer avec certitude des AirPods d'un autre appareil Apple. La plateforme
indique donc un niveau de confiance :

- `élevée` : nom AirPods ou iBeacon décodé ;
- `moyenne` : signature d'accessoire audio Apple probable ;
- `faible` : fabricant Apple détecté sans modèle précis.

La plateforme ne récupère ni le propriétaire, ni le contenu audio, ni les
communications chiffrées.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_tempo_v8_airpods_ble.zip
cd plateforme_tempo_v8_airpods_ble
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```
