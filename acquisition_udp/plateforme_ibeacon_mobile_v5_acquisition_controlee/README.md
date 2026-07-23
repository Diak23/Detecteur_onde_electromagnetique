# Plateforme iBeacon Mobile V5

## Nouvelles fonctions

- une seule courbe lorsque l'UUID est identique ou que l'adresse MAC est identique ;
- création d'un identifiant logique `Appareil_XXX` ;
- acquisition complète ;
- acquisition limitée à 30 s, 1 min, 2 min, 5 min ou durée personnalisée ;
- acquisition limitée à 100, 500, 1000 trames ou nombre personnalisé ;
- arrêt automatique et sauvegarde ;
- affichage et export de la longueur des paquets ;
- affichage du code et du nom du type de PDU ;
- statistiques des longueurs ;
- statistiques des PDU ;
- graphiques regroupés par appareil.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_ibeacon_mobile_v5_acquisition_controlee.zip
cd plateforme_ibeacon_mobile_v5_acquisition_controlee
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```

## Fichiers exportés

- `trames_energie.csv`
- `evenements_energie.csv`
- `appareils_ble.csv`
- `energie_par_appareil.csv`
- `statistiques_longueur.csv`
- `statistiques_pdu.csv`
- `parametres_acquisition.csv`
- dossier `graphes/`
