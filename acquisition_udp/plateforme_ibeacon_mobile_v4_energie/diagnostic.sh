#!/usr/bin/env bash
echo "=== Interfaces tshark ==="
tshark -D
echo
echo "=== Champs BLE ==="
tshark -G fields 2>/dev/null | grep -E "btcommon\.eir_ad\.entry\.data|btcommon\.eir_ad\.entry\.service_data|btle\.advertising_data|btle\.data|data\.data|btle\.length|nordic_ble\.rssi|nordic_ble\.channel" || true
