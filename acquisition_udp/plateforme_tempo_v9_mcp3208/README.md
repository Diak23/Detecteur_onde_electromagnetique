# Plateforme TEMPO V9 — MCP3208 et bilan de puissance

Cette version relie directement la chaîne matérielle :

```text
Antenne
→ filtre BPF-A950+ ou CBP-2250A+
→ amplificateur ZKL-33ULN-S+
→ détecteur ZX47-40-S+
→ MCP3208
→ Raspberry Pi
→ interface TEMPO
```

## Fonctions

- acquisition de deux bandes :
  - 868 MHz sur CH0 ;
  - 2,45 GHz sur CH1 ;
- lecture MCP3208 par SPI ;
- mode simulation pour tester sans matériel ;
- conversion code ADC → tension ;
- conversion tension → puissance à l'entrée du détecteur ;
- correction du gain LNA et des pertes RF ;
- puissance ramenée à l'antenne ;
- énergie RF reçue estimée ;
- alerte vert, orange et rouge ;
- acquisitions limitées par durée ou nombre d'échantillons ;
- export CSV et PNG ;
- paramètres de calibration modifiables.

## Branchement MCP3208 vers Raspberry Pi

```text
MCP3208 VDD   → 3,3 V
MCP3208 VREF  → 3,3 V
MCP3208 AGND  → GND
MCP3208 DGND  → GND
MCP3208 CLK   → GPIO11 / SCLK
MCP3208 DOUT  → GPIO9 / MISO
MCP3208 DIN   → GPIO10 / MOSI
MCP3208 CS    → GPIO8 / CE0
CH0           → sortie DC détecteur branche 868 MHz
CH1           → sortie DC détecteur branche 2,45 GHz
```

La tension appliquée à une entrée MCP3208 doit rester entre 0 V et VREF.

## Installation

```bash
cd ~/acquisition_udp
unzip plateforme_tempo_v9_mcp3208.zip
cd plateforme_tempo_v9_mcp3208
chmod +x install.sh diagnostic.sh
./install.sh
```

## Lancement

```bash
source venv/bin/activate
python3 main.py
```

## Important

Les valeurs de calibration incluses sont provisoires. Pour une mesure
scientifique, il faut injecter plusieurs puissances RF connues à 868 MHz et
2,45 GHz, enregistrer la tension de sortie du ZX47-40-S+, puis déterminer la
pente et l'ordonnée à l'origine par régression linéaire.
