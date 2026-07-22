# Détecteur iBeacon — nRF Connect Mobile

Cette application est conçue pour le fonctionnement suivant :

```text
Téléphone Android
nRF Connect Mobile
        ↓
émission iBeacon
        ↓
nRF51822 / Nordic Sniffer
        ↓
tshark sur Raspberry Pi
        ↓
interface Python
```

## Fonctions

- détection automatique de la signature iBeacon `4C 00 02 15` ;
- affichage UUID, Major, Minor et Tx Power ;
- mesure du RSSI ;
- affichage des canaux 37, 38 et 39 ;
- regroupement des trois paquets d'un même advertising ;
- mesure de l'intervalle entre advertisements ;
- calcul de la durée des événements ;
- estimation du taux de perte ;
- sauvegarde CSV et PNG.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_ibeacon_mobile_nrf.zip
cd plateforme_ibeacon_mobile_nrf

chmod +x install.sh
./install.sh
```

## Vérifier l'interface du sniffer

```bash
./diagnostic.sh
```

Repérer une ligne ressemblant à :

```text
/dev/ttyUSB0-4.4
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```

## Configuration nRF Connect Mobile

Créer un advertiser contenant :

- Manufacturer specific data ;
- Company ID Apple : `0x004C` ;
- données iBeacon commençant par `02 15` ;
- UUID sur 16 octets ;
- Major sur 2 octets ;
- Minor sur 2 octets ;
- Tx Power sur 1 octet signé.

Exemple de données fabricant, sans le Company ID :

```text
02 15
00112233445566778899AABBCCDDEEFF
0001
0001
C5
```

`C5` correspond à `-59 dBm`.
