import tkinter as tk
from tkinter import ttk
import socket
import subprocess
import re
import time
import csv
import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from datetime import datetime

UDP_IP = "0.0.0.0"
UDP_PORT = 5005
SEUIL_RSSI = -30

temps = []
rssi_values = []
puissance_values = []
messages = []

running = False
t0 = None
energie_totale = 0
dossier_acquisition = None

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)


def dbm_to_watt(dbm):
    return 10 ** ((dbm - 30) / 10)


def lire_rssi():
    try:
        sortie = subprocess.check_output(["iwconfig", "wlan0"], text=True)
        match = re.search(r"Signal level=(-?\d+)", sortie)
        if match:
            return int(match.group(1))
    except:
        return None
    return None


def creer_dossier_acquisition():
    parent = "acquisitions_udp"
    os.makedirs(parent, exist_ok=True)

    numero = 1
    while os.path.exists(f"{parent}/acquisition_{numero:03d}"):
        numero += 1

    dossier = f"{parent}/acquisition_{numero:03d}"
    os.makedirs(dossier)
    return dossier


def analyser_pics():
    pics = []
    dans_pic = False
    debut = None
    rssi_pic = []

    for i in range(len(rssi_values)):
        if not dans_pic and rssi_values[i] > SEUIL_RSSI:
            dans_pic = True
            debut = temps[i]
            rssi_pic = [rssi_values[i]]

        elif dans_pic and rssi_values[i] > SEUIL_RSSI:
            rssi_pic.append(rssi_values[i])

        elif dans_pic and rssi_values[i] <= SEUIL_RSSI:
            fin = temps[i]
            duree = fin - debut
            rssi_moy = np.mean(rssi_pic)
            puissance_moy = dbm_to_watt(rssi_moy)
            energie_pic = puissance_moy * duree

            pics.append({
                "debut": debut,
                "fin": fin,
                "duree": duree,
                "rssi_moy": rssi_moy,
                "puissance_moy": puissance_moy,
                "energie": energie_pic
            })

            dans_pic = False

    return pics


def demarrer():
    global running, t0, energie_totale, dossier_acquisition

    running = True
    t0 = time.time()
    energie_totale = 0

    temps.clear()
    rssi_values.clear()
    puissance_values.clear()
    messages.clear()

    dossier_acquisition = creer_dossier_acquisition()

    label_info.config(text=f"Acquisition en cours : {dossier_acquisition}")
    boucle_acquisition()


def arreter():
    global running
    running = False
    pics = analyser_pics()
    label_info.config(text=f"Acquisition arrêtée | Pics détectés : {len(pics)}")


def reinitialiser():
    global running, energie_totale, dossier_acquisition

    running = False
    energie_totale = 0
    dossier_acquisition = None

    temps.clear()
    rssi_values.clear()
    puissance_values.clear()
    messages.clear()

    label_message.config(text="Message reçu : ---")
    label_source.config(text="Source : ---")
    label_paquets.config(text="Paquets reçus : 0")
    label_rssi.config(text="RSSI : ---")
    label_puissance.config(text="Puissance : --- W")
    label_energie.config(text="Énergie totale : 0 J")
    label_pics.config(text="Pics détectés : 0")
    label_info.config(text="Données réinitialisées")

    ax1.clear()
    ax2.clear()
    canvas.draw()


def sauvegarder_csv():
    if dossier_acquisition is None:
        label_info.config(text="Erreur : démarre une acquisition")
        return

    chemin = f"{dossier_acquisition}/mesures_udp_rssi.csv"

    with open(chemin, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "temps_s",
            "numero_paquet",
            "message",
            "rssi_dbm",
            "puissance_w",
            "energie_totale_j"
        ])

        for i in range(len(temps)):
            writer.writerow([
                temps[i],
                i + 1,
                messages[i],
                rssi_values[i],
                puissance_values[i],
                energie_totale
            ])

    label_info.config(text=f"CSV mesures sauvegardé : {chemin}")


def sauvegarder_pics():
    if dossier_acquisition is None:
        label_info.config(text="Erreur : démarre une acquisition")
        return

    pics = analyser_pics()
    chemin = f"{dossier_acquisition}/pics_udp_rssi.csv"

    with open(chemin, "w", newline="") as f:
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
                pic["debut"],
                pic["fin"],
                pic["duree"],
                pic["rssi_moy"],
                pic["puissance_moy"],
                pic["energie"]
            ])

    label_info.config(text=f"CSV pics sauvegardé : {chemin}")


def sauvegarder_png():
    if dossier_acquisition is None:
        label_info.config(text="Erreur : démarre une acquisition")
        return

    chemin = f"{dossier_acquisition}/graphe_udp_rssi.png"
    fig.savefig(chemin, dpi=300)
    label_info.config(text=f"Graphique sauvegardé : {chemin}")


def generer_rapport():
    if dossier_acquisition is None or len(rssi_values) == 0:
        label_info.config(text="Aucune donnée pour générer le rapport")
        return

    pics = analyser_pics()
    chemin = f"{dossier_acquisition}/rapport_udp_rssi.txt"

    with open(chemin, "w") as f:
        f.write("RAPPORT ACQUISITION UDP + RSSI\n")
        f.write("==============================\n\n")
        f.write(f"Date : {datetime.now()}\n")
        f.write(f"Dossier : {dossier_acquisition}\n")
        f.write(f"Port UDP : {UDP_PORT}\n")
        f.write(f"Seuil RSSI : {SEUIL_RSSI} dBm\n\n")

        f.write("MESURES GLOBALES\n")
        f.write("----------------\n")
        f.write(f"Nombre de paquets reçus : {len(messages)}\n")
        f.write(f"Durée acquisition : {temps[-1]:.2f} s\n")
        f.write(f"RSSI moyen : {np.mean(rssi_values):.2f} dBm\n")
        f.write(f"RSSI max : {np.max(rssi_values):.2f} dBm\n")
        f.write(f"RSSI min : {np.min(rssi_values):.2f} dBm\n")
        f.write(f"Énergie totale : {energie_totale:.3e} J\n\n")

        f.write("PICS DÉTECTÉS\n")
        f.write("-------------\n")
        f.write(f"Nombre de pics : {len(pics)}\n")

        for i, pic in enumerate(pics, start=1):
            f.write(
                f"Pic {i} : début={pic['debut']:.2f}s, "
                f"fin={pic['fin']:.2f}s, "
                f"durée={pic['duree']:.2f}s, "
                f"RSSI moyen={pic['rssi_moy']:.2f} dBm, "
                f"énergie={pic['energie']:.3e} J\n"
            )

    label_info.config(text=f"Rapport généré : {chemin}")


def boucle_acquisition():
    global energie_totale

    if not running:
        return

    try:
        data, addr = sock.recvfrom(4096)

        maintenant = time.time()
        t = maintenant - t0

        message = data.decode(errors="ignore")
        rssi = lire_rssi()

        if rssi is not None:
            puissance = dbm_to_watt(rssi)

            if len(temps) >= 1:
                dt = t - temps[-1]
            else:
                dt = 0

            energie_totale += puissance * dt

            temps.append(t)
            rssi_values.append(rssi)
            puissance_values.append(puissance)
            messages.append(message)

            pics = analyser_pics()

            label_message.config(text=f"Message reçu : {message[:60]}")
            label_source.config(text=f"Source : {addr}")
            label_paquets.config(text=f"Paquets reçus : {len(messages)}")
            label_rssi.config(text=f"RSSI : {rssi} dBm")
            label_puissance.config(text=f"Puissance : {puissance:.3e} W")
            label_energie.config(text=f"Énergie totale : {energie_totale:.3e} J")
            label_pics.config(text=f"Pics détectés : {len(pics)}")

            ax1.clear()
            ax2.clear()

            ax1.plot(temps, rssi_values, label="RSSI")
            ax1.axhline(SEUIL_RSSI, linestyle="--", label="Seuil")
            ax1.set_title("RSSI reçu pendant les trames UDP")
            ax1.set_xlabel("Temps (s)")
            ax1.set_ylabel("RSSI (dBm)")
            ax1.grid(True)
            ax1.legend()

            ax2.plot(temps, puissance_values, label="Puissance")
            ax2.set_title("Puissance reçue")
            ax2.set_xlabel("Temps (s)")
            ax2.set_ylabel("Puissance (W)")
            ax2.grid(True)
            ax2.legend()

            fig.subplots_adjust(hspace=0.55)
            canvas.draw()

    except BlockingIOError:
        pass

    fenetre.after(50, boucle_acquisition)


fenetre = tk.Tk()
fenetre.title("Réception UDP Arduino + Analyse RSSI")
fenetre.geometry("1400x800")

main = ttk.Frame(fenetre, padding=10)
main.pack(fill="both", expand=True)

frame_controle = ttk.LabelFrame(main, text="Contrôles", padding=10)
frame_controle.pack(side="left", fill="y", padx=5)

ttk.Button(frame_controle, text="Démarrer acquisition", command=demarrer).pack(fill="x", pady=2)
ttk.Button(frame_controle, text="Arrêter", command=arreter).pack(fill="x", pady=2)
ttk.Button(frame_controle, text="Réinitialiser", command=reinitialiser).pack(fill="x", pady=2)
ttk.Button(frame_controle, text="Sauvegarder CSV mesures", command=sauvegarder_csv).pack(fill="x", pady=2)
ttk.Button(frame_controle, text="Sauvegarder CSV pics", command=sauvegarder_pics).pack(fill="x", pady=2)
ttk.Button(frame_controle, text="Sauvegarder PNG", command=sauvegarder_png).pack(fill="x", pady=2)
ttk.Button(frame_controle, text="Générer rapport", command=generer_rapport).pack(fill="x", pady=2)

label_info = ttk.Label(frame_controle, text="Serveur UDP prêt")
label_info.pack(anchor="w", pady=8)

frame_mesures = ttk.LabelFrame(main, text="Mesures temps réel", padding=10)
frame_mesures.pack(side="left", fill="y", padx=5)

label_message = ttk.Label(frame_mesures, text="Message reçu : ---")
label_message.pack(anchor="w")

label_source = ttk.Label(frame_mesures, text="Source : ---")
label_source.pack(anchor="w")

label_paquets = ttk.Label(frame_mesures, text="Paquets reçus : 0")
label_paquets.pack(anchor="w")

label_rssi = ttk.Label(frame_mesures, text="RSSI : ---")
label_rssi.pack(anchor="w")

label_puissance = ttk.Label(frame_mesures, text="Puissance : --- W")
label_puissance.pack(anchor="w")

label_energie = ttk.Label(frame_mesures, text="Énergie totale : 0 J")
label_energie.pack(anchor="w")

label_pics = ttk.Label(frame_mesures, text="Pics détectés : 0")
label_pics.pack(anchor="w")

frame_graphe = ttk.Frame(main, padding=10)
frame_graphe.pack(side="right", fill="both", expand=True)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
fig.subplots_adjust(hspace=0.55)

canvas = FigureCanvasTkAgg(fig, master=frame_graphe)
canvas.get_tk_widget().pack(fill="both", expand=True)

fenetre.mainloop()

