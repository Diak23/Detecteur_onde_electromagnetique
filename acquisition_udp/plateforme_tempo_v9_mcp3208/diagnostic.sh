#!/usr/bin/env bash

echo "=== Modèle Raspberry Pi ==="
cat /proc/device-tree/model 2>/dev/null || true

echo
echo "=== Interfaces SPI ==="
ls -l /dev/spidev* 2>/dev/null || echo "Aucune interface SPI détectée."

echo
echo "=== Module SPI ==="
lsmod | grep spi || true

echo
echo "=== Groupe utilisateur ==="
groups

echo
echo "=== Test import Python ==="
python3 - <<'PY'
try:
    import spidev
    print("spidev : OK")
except Exception as exc:
    print("spidev : ERREUR", exc)
PY
