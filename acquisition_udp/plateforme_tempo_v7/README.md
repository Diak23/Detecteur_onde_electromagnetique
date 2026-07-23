# Plateforme TEMPO V7

Version centrée sur le sujet **TEMPO : Temps d’Exposition aux ondes électroMagnétiques, Paramètres et Observation**.

## Nouveautés

- tableau de bord TEMPO en temps réel ;
- temps réel d’exposition RF, calculé par somme des durées radio des trames ;
- taux d’occupation du canal ;
- puissance RF reçue moyenne estimée ;
- énergie RF reçue estimée ;
- indice TEMPO expérimental de 0 à 100 ;
- alertes vert, orange et rouge ;
- résultats globaux et par appareil ;
- export `synthese_tempo.csv` et `resultats_tempo_par_appareil.csv`.

## Indice expérimental

```text
A = min(P_moyenne / P_référence, 1)
T = min(Temps_exposition / Temps_référence, 1)
Indice TEMPO = 100 × (poids_puissance × A + poids_temps × T)
```

Les références, poids et seuils sont modifiables dans le tableau de bord.
Cet indice n’est pas une limite réglementaire d’exposition.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_tempo_v7.zip
cd plateforme_tempo_v7
chmod +x install.sh diagnostic.sh
./install.sh
source venv/bin/activate
python3 main.py
```
