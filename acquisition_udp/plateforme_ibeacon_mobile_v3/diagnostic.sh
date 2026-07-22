#!/usr/bin/env bash

echo "=== Interfaces tshark ==="
tshark -D

echo
echo "=== Champs BLE disponibles ==="
tshark -G fields 2>/dev/null | grep -E \
"btcommon\.eir_ad\.entry\.data|btcommon\.eir_ad\.entry\.service_data|btle\.advertising_data|btle\.data|data\.data" \
|| true
