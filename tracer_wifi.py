import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("mesures_wifi.csv")

plt.figure(figsize=(10,4))
plt.plot(df["temps_s"], df["rssi_dbm"])
plt.xlabel("Temps (s)")
plt.ylabel("RSSI (dBm)")
plt.title("Mesure du signal Wi-Fi")
plt.grid(True)
plt.show()
