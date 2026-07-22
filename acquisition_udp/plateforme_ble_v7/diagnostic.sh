#!/usr/bin/env bash
set -u
bluetoothctl --version || true
uname -r
systemctl is-active bluetooth || true
sudo btmgmt info || true
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 SupportedInstances || true
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 ActiveInstances || true
tshark -D || true
