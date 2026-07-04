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


def analyser_pics():
    pics = []
    dans_pic = False
    debut = None
    rssi_pic = []

    for i in range(len(rssi)):

        if not dans_pic and rssi[i] > SEUIL:
            dans_pic = True
            debut = temps[i]
            rssi_pic = [rssi[i]]

        elif dans_pic and rssi[i] > SEUIL:
            rssi_pic.append(rssi[i])

        elif dans_pic and rssi[i] <= SEUIL:
            fin = temps[i]
            duree = fin - debut
            rssi_moy_pic = np.mean(rssi_pic)
            puissance_moy = dbm_to_watt(rssi_moy_pic)
            energie_pic = puissance_moy * duree

            pics.append({
                "debut_s": debut,
                "fin_s": fin,
                "duree_s": duree,
                "rssi_moy_dbm": rssi_moy_pic,
                "puissance_moy_w": puissance_moy,
                "energie_j": energie_pic
            })

            dans_pic = False

    return pics


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

    pics = analyser_pics()
    label_info.config(text=f"Acquisition arrêtée. Pics détectés : {len(pics)}")
    label_duree_pics.config(text=f"Durée moyenne des pics : {duree_moyenne_pics():.2f} s")


def reinitialiser():
    global running, nb_evenements, energie_totale

    running = False
    nb_evenements = 0
    energie_totale = 0

    temps.clear()
    rssi.clear()
    puissance.clear()

    label_rssi.config(text="RSSI instantané : ---")
    label_puissance.config(text="Puissance instantanée : --- W")
    label_moy.config(text="RSSI moyen : ---")
    label_max.config(text="RSSI max : ---")
    label_min.config(text="RSSI min : ---")
    label_nb.config(text="Nb mesures : 0")
    label_evt.config(text="Nb événements : 0")
    label_energie.config(text="Énergie totale : 0 J")
    label_duree_pics.config(text="Durée moyenne des pics : ---")

    label_info.config(text="Données réinitialisées.")

    ax1.clear()
    ax2.clear()
    canvas.draw()


def duree_moyenne_pics():
    pics = analyser_pics()
    if len(pics) == 0:
        return 0
    return np.mean([p["duree_s"] for p in pics])


def sauvegarder_csv():
    with open("mesures_wifi_reel.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["temps_s", "rssi_dbm", "puissance_w"])
        for t, r, p in zip(temps, rssi, puissance):
            writer.writerow([t, r, p])

    label_info.config(text="CSV sauvegardé : mesures_wifi_reel.csv")


def sauvegarder_csv_pics():
    pics = analyser_pics()

    with open("pics_wifi.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "numero_pic",
            "debut_s",
            "fin_s",
            "duree_s",
            "rssi_moy_dbm",
            "puissance_moy_w",
            "energie_j"
        ])

        for i, pic in enumerate(pics, start=1):
            writer.writerow([
                i,
                pic["debut_s"],
                pic["fin_s"],
                pic["duree_s"],
                pic["rssi_moy_dbm"],
                pic["puissance_moy_w"],
                pic["energie_j"]
            ])

    label_info.config(text="CSV des pics sauvegardé : pics_wifi.csv")


def sauvegarder_png():
    fig.savefig("graphe_wifi_reel.png", dpi=300)
    label_info.config(text="PNG sauvegardé : graphe_wifi_reel.png")


def generer_rapport():
    if len(rssi) == 0:
        label_info.config(text="Aucune donnée pour générer le rapport.")
        return

    pics = analyser_pics()
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
        f.write(f"Nombre d'événements : {len(pics)}\n")
        f.write(f"Puissance moyenne : {np.mean(puissance):.3e} W\n")
        f.write(f"Puissance max : {np.max(puissance):.3e} W\n")
        f.write(f"Puissance min : {np.min(puissance):.3e} W\n")
        f.write(f"Énergie totale : {energie_totale:.3e} J\n")

        if len(pics) > 0:
            f.write(f"Durée moyenne des pics : {np.mean([p['duree_s'] for p in pics]):.2f} s\n")
            f.write(f"Durée max des pics : {np.max([p['duree_s'] for p in pics]):.2f} s\n")
            f.write(f"Durée min des pics : {np.min([p['duree_s'] for p in pics]):.2f} s\n")
        else:
            f.write("Aucun pic détecté.\n")

        f.write("\nDÉTAIL DES PICS\n")
        f.write("----------------\n")

        for i, pic in enumerate(pics, start=1):
            f.write(
                f"Pic {i} : "
                f"début = {pic['debut_s']:.2f} s, "
                f"fin = {pic['fin_s']:.2f} s, "
                f"durée = {pic['duree_s']:.2f} s, "
                f"RSSI moyen = {pic['rssi_moy_dbm']:.2f} dBm, "
                f"Puissance moyenne = {pic['puissance_moy_w']:.3e} W, "
                f"Énergie = {pic['energie_j']:.3e} J\n"
            )

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

        pics = analyser_pics()

        label_rssi.config(text=f"RSSI instantané : {valeur} dBm")
        label_puissance.config(text=f"Puissance instantanée : {p_w:.3e} W")
        label_moy.config(text=f"RSSI moyen : {np.mean(rssi):.2f} dBm")
        label_max.config(text=f"RSSI max : {np.max(rssi):.2f} dBm")
        label_min.config(text=f"RSSI min : {np.min(rssi):.2f} dBm")
        label_nb.config(text=f"Nb mesures : {len(rssi)}")
        label_evt.config(text=f"Nb événements : {len(pics)}")
        label_energie.config(text=f"Énergie totale : {energie_totale:.3e} J")

        if len(pics) > 0:
            label_duree_pics.config(
                text=f"Durée moyenne des pics : {np.mean([p['duree_s'] for p in pics]):.2f} s"
            )
        else:
            label_duree_pics.config(text="Durée moyenne des pics : ---")

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

    fig.subplots_adjust(hspace=0.55)

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
ttk.Button(frame_controles, text="Sauvegarder CSV mesures", command=sauvegarder_csv).pack(fill="x", pady=2)
ttk.Button(frame_controles, text="Sauvegarder CSV pics", command=sauvegarder_csv_pics).pack(fill="x", pady=2)
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

label_duree_pics = ttk.Label(frame_mesures, text="Durée moyenne des pics : ---")
label_duree_pics.pack(anchor="w")

label_energie = ttk.Label(frame_mesures, text="Énergie totale : 0 J")
label_energie.pack(anchor="w")

frame_graphe = ttk.Frame(main, padding=10)
frame_graphe.pack(side="right", fill="both", expand=True)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
fig.subplots_adjust(hspace=0.55)

canvas = FigureCanvasTkAgg(fig, master=frame_graphe)
canvas.get_tk_widget().pack(fill="both", expand=True)

fenetre.mainloop()
