import tkinter as tk
from tkinter import ttk
import subprocess
import re
import time
import csv
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from datetime import datetime

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
    acquisition()

def arreter():
    global running
    running = False

def sauvegarder_csv():
    with open("mesures_wifi_reel.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["temps_s", "rssi_dbm"])
        for t, p in zip(temps, rssi):
            writer.writerow([t, p])
    label_info.config(text="CSV sauvegardé : mesures_wifi_reel.csv")

def sauvegarder_png():
    fig.savefig("graphe_wifi_reel.png", dpi=300)
    label_info.config(text="PNG sauvegardé : graphe_wifi_reel.png")

def generer_rapport():
    if len(rssi) == 0:
        label_info.config(text="Aucune donnée pour générer le rapport.")
        return

    duree = temps[-1] if temps else 0
    freq = frequence_var.get()

    with open("rapport_wifi.txt", "w") as f:
        f.write("RAPPORT D'ACQUISITION WI-FI\n")
        f.write("===========================\n\n")
        f.write(f"Date : {datetime.now()}\n")
        f.write(f"Fréquence sélectionnée : {freq}\n")
        f.write(f"Durée acquisition : {duree:.2f} s\n")
        f.write(f"Nombre de mesures : {len(rssi)}\n")
        f.write(f"Seuil de détection : {SEUIL} dBm\n\n")
        f.write(f"RSSI moyen : {np.mean(rssi):.2f} dBm\n")
        f.write(f"RSSI max : {np.max(rssi):.2f} dBm\n")
        f.write(f"RSSI min : {np.min(rssi):.2f} dBm\n")
        f.write(f"Nombre d'événements : {nb_evenements}\n")
        f.write(f"Énergie totale : {energie_totale:.3e} J\n")

    label_info.config(text="Rapport généré : rapport_wifi.txt")

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

        energie_totale += puissance_w * dt

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
fenetre.title("Acquisition RF Wi-Fi - Projet EEA")

main = ttk.Frame(fenetre, padding=10)
main.pack(fill="both", expand=True)

frequence_var = tk.StringVar(value="2.4 GHz")

frame_controles = ttk.LabelFrame(main, text="Contrôles", padding=10)
frame_controles.pack(side="left", fill="y", padx=5)

ttk.Label(frame_controles, text="Fréquence").pack(anchor="w")
ttk.Radiobutton(frame_controles, text="868 MHz", variable=frequence_var, value="868 MHz").pack(anchor="w")
ttk.Radiobutton(frame_controles, text="2.4 GHz", variable=frequence_var, value="2.4 GHz").pack(anchor="w")

ttk.Separator(frame_controles).pack(fill="x", pady=8)

ttk.Button(frame_controles, text="Démarrer acquisition", command=demarrer).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Arrêter", command=arreter).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Sauvegarder CSV", command=sauvegarder_csv).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Sauvegarder PNG", command=sauvegarder_png).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Générer rapport", command=generer_rapport).pack(fill="x", pady=2)

label_info = ttk.Label(frame_controles, text="")
label_info.pack(anchor="w", pady=8)

frame_mesures = ttk.LabelFrame(main, text="Mesures", padding=10)
frame_mesures.pack(side="left", fill="y", padx=5)

label_rssi = ttk.Label(frame_mesures, text="RSSI instantané : ---")
label_rssi.pack(anchor="w")

label_moy = ttk.Label(frame_mesures, text="RSSI moyen : ---")
label_moy.pack(anchor="w")

label_max = ttk.Label(frame_mesures, text="RSSI max : ---")
label_max.pack(anchor="w")

label_min = ttk.Label(frame_mesures, text="RSSI min : ---")
label_min.pack(anchor="w")

label_nb = ttk.Label(frame_mesures, text="Nb mesures : 0")
label_nb.pack(anchor="w")

label_evt = ttk.Label(frame_mesures, text="Nb événements : 0")
label_evt.pack(anchor="w")

label_energie = ttk.Label(frame_mesures, text="Énergie totale : 0 J")
label_energie.pack(anchor="w")

frame_graphe = ttk.Frame(main, padding=10)
frame_graphe.pack(side="right", fill="both", expand=True)

fig, ax = plt.subplots(figsize=(9, 5))
canvas = FigureCanvasTkAgg(fig, master=frame_graphe)
canvas.get_tk_widget().pack(fill="both", expand=True)

fenetre.mainloop()
