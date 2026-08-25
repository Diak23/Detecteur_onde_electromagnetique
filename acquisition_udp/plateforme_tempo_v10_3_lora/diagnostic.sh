#!/usr/bin/env bash

echo "=== SPI ==="
ls -l /dev/spidev* 2>/dev/null || echo "Aucun périphérique SPI."

echo
echo "=== Wi-Fi ==="
iw dev 2>/dev/null || true

echo
echo "=== Interfaces tshark ==="
tshark -D 2>/dev/null || true

echo
echo "=== Imports Python ==="
python3 - <<'PY'
for module in ("tkinter", "matplotlib", "spidev"):
    try:
        __import__(module)
        print(module, ": OK")
    except Exception as exc:
        print(module, ": ERREUR", exc)
PY
