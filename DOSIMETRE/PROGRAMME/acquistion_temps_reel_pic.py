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
puissance = []

running = False
t0 = None

SEUIL = -30
nb_evenements = 0
energie_totale = 0

def dbm_to_watt(dbm):
    return 10 ** ((dbm - 30) / 10)

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
    puissance.clear()

    label_info.config(text="Acquisition en cours...")
    acquisition()

def arreter():
    global running
    running = False
    label_info.config(text="Acquisition arrêtée.")

def reinitialiser():
    global running, nb_evenements, energie_totale

    running = False
    nb_evenements = 0
    energie_totale = 0

    temps.clear()
    rssi.clear()
    puissance.clear()

    label_rssi.config(text="RSSI instantané : ---")
    label_moy.config(text="RSSI moyen : ---")
    label_max.config(text="RSSI max : ---")
    label_min.config(text="RSSI min : ---")
    label_nb.config(text="Nb mesures : 0")
    label_evt.config(text="Nb événements : 0")
    label_energie.config(text="Énergie totale : 0 J")
    label_puissance.config(text="Puissance instantanée : --- W")
    label_info.config(text="Données réinitialisées.")

    ax1.clear()
    ax2.clear()
    canvas.draw()

def sauvegarder_csv():
    with open("mesures_wifi_reel.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["temps_s", "rssi_dbm", "puissance_w"])
        for t, r, p in zip(temps, rssi, puissance):
            writer.writerow([t, r, p])

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
        f.write("RAPPORT D'ACQUISITION RF / WI-FI\n")
        f.write("================================\n\n")
        f.write(f"Date : {datetime.now()}\n")
        f.write(f"Fréquence sélectionnée : {freq}\n")
        f.write(f"Durée acquisition : {duree:.2f} s\n")
        f.write(f"Nombre de mesures : {len(rssi)}\n")
        f.write(f"Seuil de détection : {SEUIL} dBm\n\n")

        f.write("STATISTIQUES RSSI\n")
        f.write("-----------------\n")
        f.write(f"RSSI moyen : {np.mean(rssi):.2f} dBm\n")
        f.write(f"RSSI max : {np.max(rssi):.2f} dBm\n")
        f.write(f"RSSI min : {np.min(rssi):.2f} dBm\n\n")

        f.write("ANALYSE ÉVÉNEMENTS / ÉNERGIE\n")
        f.write("----------------------------\n")
        f.write(f"Nombre d'événements : {nb_evenements}\n")
        f.write(f"Puissance moyenne : {np.mean(puissance):.3e} W\n")
        f.write(f"Puissance max : {np.max(puissance):.3e} W\n")
        f.write(f"Puissance min : {np.min(puissance):.3e} W\n")
        f.write(f"Énergie totale : {energie_totale:.3e} J\n")

    label_info.config(text="Rapport généré : rapport_wifi.txt")

def acquisition():
    global nb_evenements, energie_totale

    if not running:
        return

    valeur = lire_rssi()
    t = time.time() - t0

    if valeur is not None:
        p_w = dbm_to_watt(valeur)

        temps.append(t)
        rssi.append(valeur)
        puissance.append(p_w)

        if len(rssi) >= 2:
            if rssi[-2] <= SEUIL and rssi[-1] > SEUIL:
                nb_evenements += 1

        if len(temps) >= 2:
            dt = temps[-1] - temps[-2]
        else:
            dt = 0

        energie_totale += p_w * dt

        label_rssi.config(text=f"RSSI instantané : {valeur} dBm")
        label_puissance.config(text=f"Puissance instantanée : {p_w:.3e} W")
        label_moy.config(text=f"RSSI moyen : {np.mean(rssi):.2f} dBm")
        label_max.config(text=f"RSSI max : {np.max(rssi):.2f} dBm")
        label_min.config(text=f"RSSI min : {np.min(rssi):.2f} dBm")
        label_nb.config(text=f"Nb mesures : {len(rssi)}")
        label_evt.config(text=f"Nb événements : {nb_evenements}")
        label_energie.config(text=f"Énergie totale : {energie_totale:.3e} J")

    ax1.clear()
    ax2.clear()

    ax1.plot(temps, rssi, label="RSSI")
    ax1.axhline(SEUIL, linestyle="--", label="Seuil")

    indices_evt = []
    for i in range(1, len(rssi)):
        if rssi[i-1] <= SEUIL and rssi[i] > SEUIL:
            indices_evt.append(i)

    if indices_evt:
        temps_np = np.array(temps)
        rssi_np = np.array(rssi)

        ax1.scatter(
            temps_np[indices_evt],
            rssi_np[indices_evt],
            color="red",
            s=40,
            label="Événements"
        )

    ax1.set_title("RSSI Wi-Fi temps réel")
    ax1.set_xlabel("Temps (s)")
    ax1.set_ylabel("RSSI (dBm)")
    ax1.grid(True)
    ax1.legend()

    ax2.plot(temps, puissance, label="Puissance")
    ax2.set_title("Puissance reçue")
    ax2.set_xlabel("Temps (s)")
    ax2.set_ylabel("Puissance (W)")
    ax2.grid(True)
    ax2.legend()

    canvas.draw()
    fenetre.after(100, acquisition)

fenetre = tk.Tk()
fenetre.title("Acquisition RF Wi-Fi - Projet EEA")
fenetre.geometry("1400x800")

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
ttk.Button(frame_controles, text="Réinitialiser", command=reinitialiser).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Sauvegarder CSV", command=sauvegarder_csv).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Sauvegarder PNG", command=sauvegarder_png).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Générer rapport", command=generer_rapport).pack(fill="x", pady=2)

label_info = ttk.Label(frame_controles, text="")
label_info.pack(anchor="w", pady=8)

frame_mesures = ttk.LabelFrame(main, text="Mesures et analyse", padding=10)
frame_mesures.pack(side="left", fill="y", padx=5)

label_rssi = ttk.Label(frame_mesures, text="RSSI instantané : ---")
label_rssi.pack(anchor="w")

label_puissance = ttk.Label(frame_mesures, text="Puissance instantanée : --- W")
label_puissance.pack(anchor="w")

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

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7))
canvas = FigureCanvasTkAgg(fig, master=frame_graphe)
canvas.get_tk_widget().pack(fill="both", expand=True)

fenetre.mainloop()
