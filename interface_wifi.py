import tkinter as tk
from tkinter import ttk
import csv
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

temps = []
rssi = []

with open("mesures_wifi.csv", "r") as f:
    lecteur = csv.DictReader(f)
    for ligne in lecteur:
        temps.append(float(ligne["temps_s"]))
        rssi.append(float(ligne["rssi_dbm"]))

temps = np.array(temps)
rssi = np.array(rssi)

rssi_moy = np.mean(rssi)
rssi_max = np.max(rssi)
rssi_min = np.min(rssi)

SEUIL = -30

indices_pics = []
for i in range(1, len(rssi)):
    if rssi[i-1] <= SEUIL and rssi[i] > SEUIL:
        indices_pics.append(i)

nb_pics = len(indices_pics)

puissance_w = 10 ** ((rssi - 30) / 10)
dt = np.mean(np.diff(temps))
energie_totale = np.sum(puissance_w * dt)

fenetre = tk.Tk()
fenetre.title("Analyse Wi-Fi - Projet EEA")

frame = ttk.Frame(fenetre, padding=10)
frame.pack(fill="both", expand=True)

ttk.Label(frame, text=f"RSSI moyen : {rssi_moy:.2f} dBm").pack(anchor="w")
ttk.Label(frame, text=f"RSSI max : {rssi_max:.2f} dBm").pack(anchor="w")
ttk.Label(frame, text=f"RSSI min : {rssi_min:.2f} dBm").pack(anchor="w")
ttk.Label(frame, text=f"Nombre de pics : {nb_pics}").pack(anchor="w")
ttk.Label(frame, text=f"Energie totale : {energie_totale:.3e} J").pack(anchor="w")

fig, ax = plt.subplots(figsize=(8, 4))

ax.plot(temps, rssi, label="RSSI")

ax.scatter(
    temps[indices_pics],
    rssi[indices_pics],
    color="red",
    s=50,
    label="Pics"
)

ax.axhline(SEUIL, linestyle="--", label="Seuil")

ax.set_title("Signal Wi-Fi")
ax.set_xlabel("Temps (s)")
ax.set_ylabel("RSSI (dBm)")
ax.grid(True)
ax.legend()

canvas = FigureCanvasTkAgg(fig, master=frame)
canvas.draw()
canvas.get_tk_widget().pack(fill="both", expand=True)

fenetre.mainloop()
