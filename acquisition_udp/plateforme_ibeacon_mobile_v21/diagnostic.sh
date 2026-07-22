#!/usr/bin/env bash
echo "=== tshark ==="
tshark --version | head -n 3
echo
echo "=== interfaces ==="
tshark -D
echo
echo "=== champs bruts disponibles ==="
tshark -G fields 2>/dev/null | grep -E \
"btcommon\.eir_ad\.entry\.data|btcommon\.eir_ad\.entry\.service_data|btle\.advertising_data|btle\.data|data\.data" || true
