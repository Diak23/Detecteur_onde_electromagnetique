# Plateforme iBeacon Mobile V2

Cette version corrige l'erreur liée au champ inexistant :

```text
btcommon.eir_ad.entry.manufacturer_company_id
```

Elle détecte automatiquement les champs réellement disponibles dans votre
version de tshark puis cherche la signature iBeacon dans les données brutes.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_ibeacon_mobile_v2.zip
cd plateforme_ibeacon_mobile_v2
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```

## Configuration nRF Connect Mobile

```text
Company ID : 0x004C
Data :
021500112233445566778899AABBCCDDEEFF00010001C5
```

UUID : `00112233-4455-6677-8899-AABBCCDDEEFF`  
Major : `1`  
Minor : `1`  
Tx Power : `-59 dBm`

L'onglet **Diagnostic brut** doit afficher des données même lorsqu'aucun
iBeacon n'est reconnu. S'il reste vide, le sniffer ne reçoit pas les paquets.
