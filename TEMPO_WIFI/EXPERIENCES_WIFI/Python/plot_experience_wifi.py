import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("experience_wifi.csv")

plt.figure()
plt.plot(df["temps_s"], df["paquets_par_seconde"])
plt.xlabel("Temps (s)")
plt.ylabel("Paquets par seconde")
plt.title("Evolution du trafic Wi-Fi")
plt.grid(True)
plt.savefig("experience_paquets.png")
plt.show()

plt.figure()
plt.plot(df["temps_s"], df["debit_ko_s"])
plt.xlabel("Temps (s)")
plt.ylabel("Debit (ko/s)")
plt.title("Evolution du debit Wi-Fi")
plt.grid(True)
plt.savefig("experience_debit.png")
plt.show()
