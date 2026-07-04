#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lecture du capteur BME280 (température, humidité, pression)
sur Raspberry Pi 4 via I²C avec gestion des erreurs.
"""

import time
import sys

try:
    import board
    import busio
    import adafruit_bme280.advanced as adafruit_bme280

except ImportError as e:
    print("❌ Erreur d'importation :", e)
    print("Assure-toi d'avoir installé les librairies suivantes dans ton venv :")
    print("    pip install adafruit-circuitpython-bme280 adafruit-blinka RPi.GPIO")
    sys.exit(1)

def main():
    print("Initialisation du bus I²C...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
    except Exception as e:
        print("❌ Impossible d'accéder au bus I²C :", e)
        print("➡ Vérifie que l’I²C est activé via 'sudo raspi-config'.")
        sys.exit(1)

    print("Recherche du capteur BME280...")
    for address in [0x76, 0x77]:
        try:
            bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=address)
            print(f"✅ Capteur détecté à l’adresse 0x{address:02X}")
            break
        except ValueError:
            bme280 = None

    if bme280 is None:
        print("❌ Aucun capteur BME280 détecté.")
        print("➡ Vérifie les connexions SDA/SCL, l’alimentation (3.3 V) et refais 'i2cdetect -y 1'.")
        sys.exit(1)

    # Réglage optionnel de la pression au niveau de la mer
    bme280.sea_level_pressure = 1013.25

    print("\nLecture en cours (Ctrl+C pour quitter)...\n")
    try:
        while True:
            print(f"Température : {bme280.temperature:5.2f} °C")
            print(f"Humidité    : {bme280.humidity:5.2f} %")
            print(f"Pression    : {bme280.pressure:7.2f} hPa")
            print(f"Altitude    : {bme280.altitude:7.2f} m")
            print("-" * 40)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n Fin du programme.")
        sys.exit(0)


if __name__ == "__main__":
    main()
