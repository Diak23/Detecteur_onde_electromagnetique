import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("mesure_wifi.csv")

plt.figure()
plt.plot(df["temps_s"], df["paquets_par_seconde"])
plt.xlabel("Temps (s)")
plt.ylabel("Paquets par seconde")
plt.title("Activité Wi-Fi reçue par la Raspberry Pi")
plt.grid(True)
plt.savefig("graph_wifi.png")
plt.show()
