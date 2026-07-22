#!/usr/bin/env bash
set -u

echo "=== Interfaces tshark ==="
tshark -D || true

echo
echo "=== nRF Sniffer ==="
tshark -D | grep -i -E "nRF|ttyUSB" || true

echo
echo "=== Ports série ==="
ls -l /dev/ttyUSB* 2>/dev/null || true

echo
echo "=== Groupes utilisateur ==="
groups
