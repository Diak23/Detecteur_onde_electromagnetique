import time
import board
import adafruit_bme280.advanced as adafruit_bme280

# Initialisation I2C
i2c = board.I2C()  # SDA, SCL automatiques
bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x76)

# Optionnel : si ton capteur a une adresse différente
# bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x77)

# Paramètres (facultatif)
bme280.sea_level_pressure = 1013.25

while True:
    print(f"Température : {bme280.temperature:.1f} °C")
    print(f"Humidité    : {bme280.humidity:.1f} %")
    print(f"Pression    : {bme280.pressure:.1f} hPa")
    print(f"Altitude    : {bme280.altitude:.2f} m")
    print("-" * 30)
    time.sleep(2)
