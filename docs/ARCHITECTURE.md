# Architecture et interprétation

[Retour à l’accueil](../README.md) · [Démonstration](DEMONSTRATION.md)

## Deux voies d’observation complémentaires

La voie analogique suit la chaîne **antenne → filtre → LNA → détecteur RF → MCP3208 → Raspberry Pi**. Elle estime une puissance au connecteur d’antenne à partir de la tension du détecteur et d’un bilan de gain calibré.

La voie numérique exploite le RSSI Wi-Fi de la liaison active et des trames BLE capturées. Elle donne des informations protocolaires complémentaires ; ses observations ne doivent pas être confondues avec une mesure analogique large bande.

| Élément logiciel | Responsabilité |
|---|---|
| `main.py` | Interface, orchestration, conversions, affichage et exports |
| `drivers/mcp3208_driver.py` | Lecture des codes ADC par SPI |
| `drivers/wifi_driver.py` | Extraction du RSSI via `iw dev <interface> link` |
| `drivers/ble_tshark_driver.py` | Capture et extraction des champs BLE via tshark |

## Grandeurs et calculs présents dans le code

| Grandeur | Calcul ou origine | Condition d’interprétation |
|---|---|---|
| Tension ADC | `code × VREF / 4095` | Convention utilisée par le code ; référence et transfert ADC à vérifier pour la précision visée |
| Puissance détecteur | `P_ref + (V − V_ref) / pente` | Pente en V/dB et référence issues d’une calibration |
| Gain net | `gain_LNA − pertes_filtre − pertes_câble − pertes_commutateur` | Valeurs dépendantes de la fréquence et du montage |
| Puissance antenne | `P_detecteur_dBm − gain_net_dB` | Estimation ramenée au connecteur d’antenne |
| Puissance en watts | `10 ** ((P_dBm − 30) / 10)` | Conversion d’un niveau en dBm |
| Énergie estimée | `somme(P_W × durée_s)` | Dépend de la validité de la puissance et de la durée retenues |
| RSSI | Valeur rapportée par l’interface ou le sniffer | Dépend du récepteur, de ses mises à jour et de sa calibration |

## Traçabilité

Chaque enregistrement comprend notamment `source`, `band`, `device`, `elapsed_s`, `duration_s`, `power_w`, `energy_j` et `simulated`.

Le champ `simulated` distingue données synthétiques et observations physiques. Pour comparer des résultats, conserver aussi les réglages de calibration, la configuration matérielle et les conditions de l’essai. La synthèse exportée ne remplace pas une fiche complète de calibration.

<a id="limites-et-validation"></a>
## Limites et validation

- **Simulation :** signal synthétique sinusoïdal bruité, utile pour découvrir les fonctions. Elle ne valide pas le matériel.
- **Chaîne RF :** les réglages par défaut ne constituent pas une calibration mesurée. Vérifier adaptation, gain, pertes, saturation, dynamique et réponse temporelle.
- **Échantillonnage :** le code utilise la période configurée pour l’énergie RF/Wi-Fi. Le temps effectif entre acquisitions peut différer ; les impulsions brèves peuvent être manquées.
- **Wi-Fi :** le RSSI de la liaison active n’est ni un relevé exhaustif des paquets ni une mesure du temps réel d’émission.
- **BLE :** la durée est approximée à partir de la longueur selon une hypothèse à 1 Mbit/s. Les pertes de capture, les canaux observés et les variantes de PHY limitent l’interprétation.
- **Identification BLE :** les noms Apple/AirPods/iBeacon sont issus d’heuristiques ; ils ne garantissent pas l’identité d’un appareil.
- **Cumul :** ne pas interpréter la somme des sources comme une exposition totale ; les sources peuvent se recouvrir ou mélanger simulation et observation.
- **Alertes :** seuils logiciels de démonstration ; ils ne constituent pas des seuils sanitaires validés.
- **Portée :** énergie RF estimée, sans mesure d’énergie absorbée ni conclusion sur l’exposition d’une personne.

Cette contribution documente le code existant. Elle ne constitue pas un audit du logiciel ni une validation expérimentale. Le parcours graphique doit encore être exécuté sur la machine cible, puis les mesures physiques confrontées à une référence.

## Organisation future

Conserver une application de référence, identifier les variantes par des versions Git et sélectionner quelques exemples documentés facilitera la réutilisation. Avant une diffusion large, traiter les fichiers personnels et les éventuels secrets déjà présents dans le dépôt, y compris leur historique. Ces opérations sont distinctes de cette contribution documentaire.
