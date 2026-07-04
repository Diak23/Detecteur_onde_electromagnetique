import tkinter as tk
from tkinter import ttk
import subprocess
import re
import time
import csv
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

temps = []
rssi = []
running = False
t0 = None

SEUIL = -30
nb_evenements = 0
energie_totale = 0

def lire_rssi():
    sortie = subprocess.check_output(["iwconfig", "wlan0"], text=True)
    match = re.search(r"Signal level=(-?\d+)", sortie)
    if match:
        return int(match.group(1))
    return None

def demarrer():
    global running, t0, nb_evenements, energie_totale

    running = True
    t0 = time.time()
    nb_evenements = 0
    energie_totale = 0

    temps.clear()
    rssi.clear()

    label_evt.config(text="Nb événements : 0")
    label_energie.config(text="Énergie totale : 0 J")

    acquisition()

def arreter():
    global running
    running = False

def sauvegarder():
    with open("mesures_wifi_reel.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["temps_s", "rssi_dbm"])
        for t, p in zip(temps, rssi):
            writer.writerow([t, p])

    label_info.config(text="Fichier sauvegardé : mesures_wifi_reel.csv")

def sauvegarder_png():
    fig.savefig("graphe_wifi_reel.png", dpi=300)
    label_info.config(text="Graphique sauvegardé : graphe_wifi_reel.png")

def acquisition():
    global nb_evenements, energie_totale

    if not running:
        return

    valeur = lire_rssi()
    t = time.time() - t0

    if valeur is not None:
        temps.append(t)
        rssi.append(valeur)

        if len(rssi) >= 2:
            if rssi[-2] <= SEUIL and rssi[-1] > SEUIL:
                nb_evenements += 1

        puissance_w = 10 ** ((valeur - 30) / 10)

        if len(temps) >= 2:
            dt = temps[-1] - temps[-2]
        else:
            dt = 0

        energie = puissance_w * dt
        energie_totale += energie

        label_rssi.config(text=f"RSSI instantané : {valeur} dBm")
        label_moy.config(text=f"RSSI moyen : {np.mean(rssi):.2f} dBm")
        label_max.config(text=f"RSSI max : {np.max(rssi):.2f} dBm")
        label_min.config(text=f"RSSI min : {np.min(rssi):.2f} dBm")
        label_nb.config(text=f"Nb mesures : {len(rssi)}")
        label_evt.config(text=f"Nb événements : {nb_evenements}")
        label_energie.config(text=f"Énergie totale : {energie_totale:.3e} J")

    ax.clear()

    ax.plot(temps, rssi, label="RSSI")
    ax.axhline(SEUIL, linestyle="--", label="Seuil")

    indices_evt = []
    for i in range(1, len(rssi)):
        if rssi[i-1] <= SEUIL and rssi[i] > SEUIL:
            indices_evt.append(i)

    if indices_evt:
        temps_np = np.array(temps)
        rssi_np = np.array(rssi)

        ax.scatter(
            temps_np[indices_evt],
            rssi_np[indices_evt],
            color="red",
            s=50,
            label="Événements"
        )

    ax.set_title("Acquisition Wi-Fi temps réel")
    ax.set_xlabel("Temps (s)")
    ax.set_ylabel("RSSI (dBm)")
    ax.grid(True)
    ax.legend()

    canvas.draw()

    fenetre.after(100, acquisition)

fenetre = tk.Tk()
fenetre.title("Acquisition Wi-Fi temps réel")

frame = ttk.Frame(fenetre, padding=10)
frame.pack(fill="both", expand=True)

ttk.Button(frame, text="Démarrer acquisition", command=demarrer).pack(anchor="w")
ttk.Button(frame, text="Arrêter", command=arreter).pack(anchor="w")
ttk.Button(frame, text="Sauvegarder CSV", command=sauvegarder).pack(anchor="w")
ttk.Button(frame, text="Sauvegarder PNG", command=sauvegarder_png).pack(anchor="w")

label_info = ttk.Label(frame, text="")
label_info.pack(anchor="w")

label_rssi = ttk.Label(frame, text="RSSI instantané : ---")
label_rssi.pack(anchor="w")

label_moy = ttk.Label(frame, text="RSSI moyen : ---")
label_moy.pack(anchor="w")

label_max = ttk.Label(frame, text="RSSI max : ---")
label_max.pack(anchor="w")

label_min = ttk.Label(frame, text="RSSI min : ---")
label_min.pack(anchor="w")

label_nb = ttk.Label(frame, text="Nb mesures : 0")
label_nb.pack(anchor="w")

label_evt = ttk.Label(frame, text="Nb événements : 0")
label_evt.pack(anchor="w")

label_energie = ttk.Label(frame, text="Énergie totale : 0 J")
label_energie.pack(anchor="w")

fig, ax = plt.subplots(figsize=(8, 4))
canvas = FigureCanvasTkAgg(fig, master=frame)
canvas.get_tk_widget().pack(fill="both", expand=True)

fenetre.mainloop()
