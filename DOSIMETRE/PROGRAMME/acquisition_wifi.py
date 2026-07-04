import subprocess
import re
import time
import csv

N = 1000

with open("mesures_wifi.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["temps_s", "rssi_dbm"])

    t0 = time.time()

    for i in range(N):

        sortie = subprocess.check_output(
            ["iwconfig", "wlan0"],
            text=True
        )

        match = re.search(r"Signal level=(-?\d+)", sortie)

        if match:
            rssi = int(match.group(1))
        else:
            rssi = None

        temps = time.time() - t0

        writer.writerow([temps, rssi])

        print(i, temps, rssi)

        time.sleep(0.1)
