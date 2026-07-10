import tkinter as tk
from tkinter import ttk
import subprocess
import re
import time
import socket
import csv
import os
import numpy as np
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


UDP_IP = "0.0.0.0"
UDP_PORT = 5005
SEUIL_RSSI = -30

TECH_WIFI = "Wi-Fi 2.4 GHz"
TECH_BLUETOOTH = "Bluetooth"
TECH_LORA = "LoRa 868 MHz"
TECH_WIFI_BEACONS = "Beacons Wi-Fi"
TECH_BLE_BEACONS = "Beacons BLE"

running = False
mode_selectionne = TECH_WIFI

t0 = None
energie_totale = 0
dossier_acquisition = None

temps = []
rssi_values = []
puissance_values = []
snr_values = []
messages = []

bluetooth_devices = []

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)


def dbm_to_watt(dbm):
    return 10 ** ((dbm - 30) / 10)


def lire_rssi_wifi():
    try:
        sortie = subprocess.check_output(["iwconfig", "wlan0"], text=True)
        match = re.search(r"Signal level=(-?\d+)", sortie)
        if match:
            return int(match.group(1))
    except Exception:
        return None
    return None


def scanner_bluetooth_simple():
    global bluetooth_devices

    try:
        subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, text=True)
        subprocess.run(["bluetoothctl", "scan", "on"], capture_output=True, text=True, timeout=2)

        sortie = subprocess.check_output(
            ["bluetoothctl", "devices"],
            text=True,
            stderr=subprocess.DEVNULL
        )

        appareils = []

        for ligne in sortie.splitlines():
            if ligne.startswith("Device"):
                appareils.append(ligne.replace("Device ", ""))

        bluetooth_devices = appareils
        return appareils

    except Exception:
        bluetooth_devices = []
        return []


try:
    from LoRaRF import SX127x
    LORA_DISPONIBLE = True
except Exception:
    LORA_DISPONIBLE = False

lora = None


def initialiser_lora():
    global lora

    if not LORA_DISPONIBLE:
        label_etat.config(text="LoRaRF non installé")
        return False

    try:
        lora = SX127x()
        lora.begin()
        lora.setFrequency(868000000)
        lora.setSpreadingFactor(7)
        lora.setBandwidth(125000)
        lora.setCodeRate(5)
        lora.setSyncWord(0x12)
        lora.setRxGain(lora.RX_GAIN_BOOSTED)

        label_etat.config(text="LoRa initialisé sur 868 MHz")
        return True

    except Exception as e:
        label_etat.config(text=f"Erreur LoRa : {e}")
        return False


def lire_lora():
    if lora is None:
        return None, None, None

    try:
        lora.request()

        if lora.available():
            message = ""

            while lora.available():
                message += chr(lora.read())

            rssi = lora.packetRssi()
            snr = lora.snr()

            return message, rssi, snr

    except Exception as e:
        return f"Erreur LoRa : {e}", None, None

    return None, None, None


def lire_beacon_wifi():
    try:
        commande = [
            "sudo", "tshark",
            "-I",
            "-i", "wlan0mon",
            "-Y", "wlan.fc.type_subtype == 8",
            "-T", "fields",
            "-e", "wlan.ssid",
            "-e", "wlan.sa",
            "-e", "wlan_radio.signal_dbm",
            "-e", "wlan_radio.channel",
            "-c", "1"
        ]

        sortie = subprocess.check_output(
            commande,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3
        )

        champs = sortie.strip().split("\t")

        if len(champs) >= 4:
            ssid = champs[0]
            mac = champs[1]
            rssi = int(champs[2])
            canal = champs[3]

            message = f"SSID={ssid} | MAC={mac} | Canal={canal}"
            return message, rssi

    except Exception:
        return None, None

    return None, None


def lire_beacon_ble_btmon():
    try:
        subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, text=True)
        subprocess.Popen(
            ["bluetoothctl", "scan", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True
        )

        process = subprocess.Popen(
            ["sudo", "btmon"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )

        adresse = "inconnue"
        nom = "BLE"
        rssi = None

        debut = time.time()

        while time.time() - debut < 4:
            ligne = process.stdout.readline()

            if not ligne:
                continue

            ligne = ligne.strip()

            if "Address:" in ligne:
                match_addr = re.search(r"Address:\s*([0-9A-Fa-f:]{17})", ligne)
                if match_addr:
                    adresse = match_addr.group(1)

            if "Name (complete):" in ligne:
                nom = ligne.replace("Name (complete):", "").strip()

            if "RSSI:" in ligne:
                match_rssi = re.search(r"RSSI:\s*(-?\d+)", ligne)
                if match_rssi:
                    rssi = int(match_rssi.group(1))
                    break

        process.terminate()

        if rssi is not None:
            message = f"Beacon BLE | MAC={adresse} | Nom={nom}"
            label_bluetooth.config(text=f"Beacon BLE : {adresse} | RSSI : {rssi} dBm")
            return message, rssi

    except Exception as e:
        label_etat.config(text=f"Erreur Beacons BLE btmon : {e}")
        return None, None

    label_etat.config(text="Aucun beacon BLE détecté")
    return None, None


def lire_beacon_ble():
    return lire_beacon_ble_btmon()


def creer_dossier_acquisition():
    parent = "acquisitions_radio_v2"
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


def ajouter_mesure(rssi, message="", snr=""):
    global energie_totale

    t = time.time() - t0
    puissance = dbm_to_watt(rssi)

    if len(temps) >= 1:
        dt = t - temps[-1]
    else:
        dt = 0

    energie_totale += puissance * dt

    temps.append(t)
    rssi_values.append(rssi)
    puissance_values.append(puissance)
    snr_values.append(snr)
    messages.append(message)

    mettre_a_jour_interface()
    mettre_a_jour_graphes()


def demarrer():
    global running, t0, energie_totale, dossier_acquisition

    running = True
    t0 = time.time()
    energie_totale = 0

    temps.clear()
    rssi_values.clear()
    puissance_values.clear()
    snr_values.clear()
    messages.clear()

    dossier_acquisition = creer_dossier_acquisition()

    if mode_selectionne == TECH_LORA:
        initialiser_lora()

    label_etat.config(text=f"Acquisition démarrée : {dossier_acquisition}")
    boucle_acquisition()


def arreter():
    global running
    running = False
    sauvegarde_automatique()
    label_etat.config(text="Acquisition arrêtée + sauvegarde automatique effectuée")


def reinitialiser():
    global energie_totale

    energie_totale = 0

    temps.clear()
    rssi_values.clear()
    puissance_values.clear()
    snr_values.clear()
    messages.clear()

    label_message.config(text="Message : ---")
    label_source.config(text="Source : ---")
    label_rssi.config(text="RSSI instantané : ---")
    label_rssi_moy.config(text="RSSI moyen : ---")
    label_rssi_max.config(text="RSSI max : ---")
    label_rssi_min.config(text="RSSI min : ---")
    label_puissance.config(text="Puissance : ---")
    label_energie.config(text="Énergie totale : 0 J")
    label_pics.config(text="Pics détectés : 0")
    label_mesures.config(text="Nombre de mesures : 0")
    label_duree.config(text="Durée : 0 s")
    label_snr.config(text="SNR : ---")
    label_bluetooth.config(text="Appareils Bluetooth : 0")

    ax_rssi.clear()
    ax_puissance.clear()
    ax_snr.clear()

    configurer_graphes()
    canvas.draw()


def changer_mode():
    global mode_selectionne

    mode_selectionne = combo_tech.get()
    label_mode.config(text=f"Technologie sélectionnée : {mode_selectionne}")
    reinitialiser()

    if mode_selectionne == TECH_LORA:
        initialiser_lora()


def boucle_acquisition():
    if not running:
        return

    if mode_selectionne == TECH_WIFI:
        try:
            data, addr = sock.recvfrom(4096)
            message = data.decode(errors="ignore")
            rssi = lire_rssi_wifi()

            if rssi is not None:
                label_source.config(text=f"Source : {addr}")
                ajouter_mesure(rssi, message, "")

        except BlockingIOError:
            pass

    elif mode_selectionne == TECH_BLUETOOTH:
        appareils = scanner_bluetooth_simple()
        rssi_simule = -70

        message = f"{len(appareils)} appareil(s) détecté(s)"
        label_source.config(text="Source : bluetoothctl")
        label_bluetooth.config(text=f"Appareils Bluetooth : {len(appareils)}")
        ajouter_mesure(rssi_simule, message, "")

    elif mode_selectionne == TECH_LORA:
        message, rssi, snr = lire_lora()

        if message is not None and rssi is not None:
            label_source.config(text="Source : module LoRa SX1276/RFM95")
            ajouter_mesure(rssi, message, snr)

    elif mode_selectionne == TECH_WIFI_BEACONS:
        message, rssi = lire_beacon_wifi()

        if message is not None and rssi is not None:
            label_source.config(text="Source : beacons Wi-Fi avec tshark")
            ajouter_mesure(rssi, message, "")

    elif mode_selectionne == TECH_BLE_BEACONS:
        message, rssi = lire_beacon_ble()

        if message is not None and rssi is not None:
            label_source.config(text="Source : Beacons BLE avec btmon")
            ajouter_mesure(rssi, message, "")

    fenetre.after(500, boucle_acquisition)


def nom_mode_fichier():
    return mode_selectionne.replace(" ", "_").replace("/", "_")


def sauvegarder_csv():
    if dossier_acquisition is None:
        label_etat.config(text="Erreur : aucune acquisition")
        return

    chemin = f"{dossier_acquisition}/mesures_{nom_mode_fichier()}.csv"

    with open(chemin, "w", newline="") as f:
        writer = csv.writer(f)

        writer.writerow([
            "temps_s",
            "mode",
            "message",
            "rssi_dbm",
            "puissance_w",
            "snr_db",
            "energie_totale_j"
        ])

        for i in range(len(temps)):
            writer.writerow([
                temps[i],
                mode_selectionne,
                messages[i],
                rssi_values[i],
                puissance_values[i],
                snr_values[i],
                energie_totale
            ])

    label_etat.config(text=f"CSV sauvegardé : {chemin}")


def sauvegarder_pics():
    if dossier_acquisition is None:
        label_etat.config(text="Erreur : aucune acquisition")
        return

    chemin = f"{dossier_acquisition}/pics_{nom_mode_fichier()}.csv"
    pics = analyser_pics()

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

    label_etat.config(text=f"CSV pics sauvegardé : {chemin}")


def sauvegarder_png():
    if dossier_acquisition is None:
        label_etat.config(text="Erreur : aucune acquisition")
        return

    chemin = f"{dossier_acquisition}/graphes_{nom_mode_fichier()}.png"
    fig.savefig(chemin, dpi=300)

    label_etat.config(text=f"Graphes sauvegardés : {chemin}")


def generer_rapport():
    if dossier_acquisition is None or len(rssi_values) == 0:
        label_etat.config(text="Aucune donnée pour le rapport")
        return

    chemin = f"{dossier_acquisition}/rapport_{nom_mode_fichier()}.txt"
    pics = analyser_pics()

    snr_num = []
    for s in snr_values:
        try:
            snr_num.append(float(s))
        except Exception:
            pass

    with open(chemin, "w") as f:
        f.write("RAPPORT D'ACQUISITION RADIO V2\n")
        f.write("==============================\n\n")
        f.write(f"Date : {datetime.now()}\n")
        f.write(f"Technologie : {mode_selectionne}\n")
        f.write(f"Dossier : {dossier_acquisition}\n")
        f.write(f"Seuil RSSI : {SEUIL_RSSI} dBm\n\n")

        f.write("STATISTIQUES\n")
        f.write("------------\n")
        f.write(f"Nombre de mesures : {len(rssi_values)}\n")
        f.write(f"Durée : {temps[-1]:.2f} s\n")
        f.write(f"RSSI moyen : {np.mean(rssi_values):.2f} dBm\n")
        f.write(f"RSSI max : {np.max(rssi_values):.2f} dBm\n")
        f.write(f"RSSI min : {np.min(rssi_values):.2f} dBm\n")
        f.write(f"Énergie totale : {energie_totale:.3e} J\n")
        f.write(f"Nombre de pics : {len(pics)}\n\n")

        if len(snr_num) > 0:
            f.write(f"SNR moyen : {np.mean(snr_num):.2f} dB\n\n")

        f.write("PICS DÉTECTÉS\n")
        f.write("-------------\n")

        for i, pic in enumerate(pics, start=1):
            f.write(
                f"Pic {i} : début={pic['debut']:.2f}s, "
                f"fin={pic['fin']:.2f}s, "
                f"durée={pic['duree']:.2f}s, "
                f"RSSI moyen={pic['rssi_moy']:.2f} dBm, "
                f"énergie={pic['energie']:.3e} J\n"
            )

    label_etat.config(text=f"Rapport généré : {chemin}")


def sauvegarde_automatique():
    if dossier_acquisition is not None and len(rssi_values) > 0:
        sauvegarder_csv()
        sauvegarder_pics()
        sauvegarder_png()
        generer_rapport()


def mettre_a_jour_interface():
    pics = analyser_pics()

    label_message.config(text=f"Message : {messages[-1][:60]}")
    label_rssi.config(text=f"RSSI instantané : {rssi_values[-1]} dBm")
    label_puissance.config(text=f"Puissance : {puissance_values[-1]:.3e} W")
    label_energie.config(text=f"Énergie totale : {energie_totale:.3e} J")
    label_pics.config(text=f"Pics détectés : {len(pics)}")
    label_mesures.config(text=f"Nombre de mesures : {len(rssi_values)}")
    label_duree.config(text=f"Durée : {temps[-1]:.2f} s")

    label_rssi_moy.config(text=f"RSSI moyen : {np.mean(rssi_values):.2f} dBm")
    label_rssi_max.config(text=f"RSSI max : {np.max(rssi_values):.2f} dBm")
    label_rssi_min.config(text=f"RSSI min : {np.min(rssi_values):.2f} dBm")

    if snr_values[-1] != "":
        label_snr.config(text=f"SNR : {snr_values[-1]} dB")
    else:
        label_snr.config(text="SNR : ---")


def configurer_graphes():
    ax_rssi.set_title("RSSI en temps réel")
    ax_rssi.set_xlabel("Temps (s)")
    ax_rssi.set_ylabel("RSSI (dBm)")
    ax_rssi.grid(True)

    ax_puissance.set_title("Puissance reçue")
    ax_puissance.set_xlabel("Temps (s)")
    ax_puissance.set_ylabel("Puissance (W)")
    ax_puissance.grid(True)

    ax_snr.set_title("SNR LoRa")
    ax_snr.set_xlabel("Temps (s)")
    ax_snr.set_ylabel("SNR (dB)")
    ax_snr.grid(True)


def mettre_a_jour_graphes():
    ax_rssi.clear()
    ax_puissance.clear()
    ax_snr.clear()

    ax_rssi.plot(temps, rssi_values, label="RSSI")
    ax_rssi.axhline(SEUIL_RSSI, linestyle="--", label="Seuil pic")
    ax_rssi.legend()

    ax_puissance.plot(temps, puissance_values, label="Puissance")
    ax_puissance.legend()

    snr_num = []
    temps_snr = []

    for i, s in enumerate(snr_values):
        try:
            snr_num.append(float(s))
            temps_snr.append(temps[i])
        except Exception:
            pass

    if len(snr_num) > 0:
        ax_snr.plot(temps_snr, snr_num, label="SNR")
        ax_snr.legend()

    configurer_graphes()
    fig.subplots_adjust(hspace=0.65)
    canvas.draw()


fenetre = tk.Tk()
fenetre.title("Détecteur Radio Multi-Technologies V2")
fenetre.geometry("1450x850")

style = ttk.Style()
style.theme_use("clam")

main = ttk.Frame(fenetre, padding=10)
main.pack(fill="both", expand=True)

panel = ttk.Frame(main)
panel.pack(side="left", fill="y", padx=5)

titre = ttk.Label(panel, text="Détecteur Radio V2", font=("Arial", 18, "bold"))
titre.pack(pady=5)

frame_tech = ttk.LabelFrame(panel, text="Technologie", padding=10)
frame_tech.pack(fill="x", pady=5)

combo_tech = ttk.Combobox(
    frame_tech,
    values=[
        TECH_WIFI,
        TECH_BLUETOOTH,
        TECH_LORA,
        TECH_WIFI_BEACONS,
        TECH_BLE_BEACONS
    ],
    state="readonly"
)
combo_tech.current(0)
combo_tech.pack(fill="x", pady=4)

ttk.Button(frame_tech, text="Valider sélection", command=changer_mode).pack(fill="x", pady=4)

label_mode = ttk.Label(frame_tech, text=f"Technologie sélectionnée : {TECH_WIFI}")
label_mode.pack(anchor="w", pady=4)

frame_cmd = ttk.LabelFrame(panel, text="Commandes", padding=10)
frame_cmd.pack(fill="x", pady=5)

ttk.Button(frame_cmd, text="Démarrer", command=demarrer).pack(fill="x", pady=2)
ttk.Button(frame_cmd, text="Arrêter + sauvegarder", command=arreter).pack(fill="x", pady=2)
ttk.Button(frame_cmd, text="Réinitialiser", command=reinitialiser).pack(fill="x", pady=2)

frame_save = ttk.LabelFrame(panel, text="Sauvegardes", padding=10)
frame_save.pack(fill="x", pady=5)

ttk.Button(frame_save, text="CSV mesures", command=sauvegarder_csv).pack(fill="x", pady=2)
ttk.Button(frame_save, text="CSV pics", command=sauvegarder_pics).pack(fill="x", pady=2)
ttk.Button(frame_save, text="Graphes PNG", command=sauvegarder_png).pack(fill="x", pady=2)
ttk.Button(frame_save, text="Rapport TXT", command=generer_rapport).pack(fill="x", pady=2)

frame_info = ttk.LabelFrame(panel, text="État", padding=10)
frame_info.pack(fill="x", pady=5)

label_etat = ttk.Label(frame_info, text="En attente")
label_etat.pack(anchor="w")

label_source = ttk.Label(frame_info, text="Source : ---")
label_source.pack(anchor="w")

centre = ttk.Frame(main)
centre.pack(side="left", fill="both", expand=True, padx=5)

frame_stats = ttk.LabelFrame(centre, text="Statistiques temps réel", padding=10)
frame_stats.pack(fill="x", pady=5)

label_message = ttk.Label(frame_stats, text="Message : ---")
label_message.grid(row=0, column=0, sticky="w", padx=5, pady=2)

label_rssi = ttk.Label(frame_stats, text="RSSI instantané : ---")
label_rssi.grid(row=1, column=0, sticky="w", padx=5, pady=2)

label_rssi_moy = ttk.Label(frame_stats, text="RSSI moyen : ---")
label_rssi_moy.grid(row=2, column=0, sticky="w", padx=5, pady=2)

label_rssi_max = ttk.Label(frame_stats, text="RSSI max : ---")
label_rssi_max.grid(row=3, column=0, sticky="w", padx=5, pady=2)

label_rssi_min = ttk.Label(frame_stats, text="RSSI min : ---")
label_rssi_min.grid(row=4, column=0, sticky="w", padx=5, pady=2)

label_puissance = ttk.Label(frame_stats, text="Puissance : ---")
label_puissance.grid(row=1, column=1, sticky="w", padx=40, pady=2)

label_energie = ttk.Label(frame_stats, text="Énergie totale : 0 J")
label_energie.grid(row=2, column=1, sticky="w", padx=40, pady=2)

label_pics = ttk.Label(frame_stats, text="Pics détectés : 0")
label_pics.grid(row=3, column=1, sticky="w", padx=40, pady=2)

label_mesures = ttk.Label(frame_stats, text="Nombre de mesures : 0")
label_mesures.grid(row=4, column=1, sticky="w", padx=40, pady=2)

label_duree = ttk.Label(frame_stats, text="Durée : 0 s")
label_duree.grid(row=5, column=1, sticky="w", padx=40, pady=2)

label_snr = ttk.Label(frame_stats, text="SNR : ---")
label_snr.grid(row=5, column=0, sticky="w", padx=5, pady=2)

label_bluetooth = ttk.Label(frame_stats, text="Appareils Bluetooth : 0")
label_bluetooth.grid(row=6, column=0, sticky="w", padx=5, pady=2)

frame_graph = ttk.LabelFrame(centre, text="Graphes", padding=10)
frame_graph.pack(fill="both", expand=True, pady=5)

fig, (ax_rssi, ax_puissance, ax_snr) = plt.subplots(3, 1, figsize=(10, 8))
fig.subplots_adjust(hspace=0.65)

canvas = FigureCanvasTkAgg(fig, master=frame_graph)
canvas.get_tk_widget().pack(fill="both", expand=True)

configurer_graphes()

fenetre.mainloop()
