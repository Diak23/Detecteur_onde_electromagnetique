#!/usr/bin/env bash
set -u
echo '=== Bluetooth ==='
systemctl is-active bluetooth || true
sudo btmgmt info || true
echo '=== Advertising ==='
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 SupportedInstances 2>/dev/null || true
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 ActiveInstances 2>/dev/null || true
echo '=== tshark ==='
tshark -D 2>/dev/null || true
