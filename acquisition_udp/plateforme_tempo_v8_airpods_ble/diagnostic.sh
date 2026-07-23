#!/usr/bin/env bash

echo "=== Interfaces tshark ==="
tshark -D

echo
echo "=== Champs BLE nécessaires ==="
tshark -G fields 2>/dev/null | grep -E \
"btcommon\.eir_ad\.entry\.data|btcommon\.eir_ad\.entry\.service_data|btle\.advertising_data|btle\.data|data\.data|btle\.length|btle\.advertising_header\.pdu_type|nordic_ble\.rssi|nordic_ble\.channel|btcommon\.eir_ad\.entry\.device_name|btcommon\.eir_ad\.entry\.company_id" \
|| true
