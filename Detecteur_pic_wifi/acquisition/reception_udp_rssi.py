import socket
import subprocess
import re
import time
import csv
import os
from datetime import datetime

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

SEUIL_RSSI = -30

def creer_dossier_acquisition():
    parent = "acquisitions_udp"
    os.makedirs(parent, exist_ok=True)

    numero = 1
    while os.path.exists(f"{parent}/acquisition_{numero:03d}"):
        numero += 1

    dossier = f"{parent}/acquisition_{numero:03d}"
    os.makedirs(dossier)
    return dossier

def lire_rssi():
    try:
        sortie = subprocess.check_output(["iwconfig", "wlan0"], text=True)
        match = re.search(r"Signal level=(-?\d+)", sortie)
        if match:
            return int(match.group(1))
    except Exception:
        return None

    return None

def dbm_to_watt(dbm):
    return 10 ** ((dbm - 30) / 10)

dossier = creer_dossier_acquisition()

fichier_mesures = f"{dossier}/mesures_udp_rssi.csv"
fichier_pics = f"{dossier}/pics_udp_rssi.csv"
fichier_rapport = f"{dossier}/rapport_udp_rssi.txt"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("Serveur UDP prêt")
print("En attente des trames Arduino...")
print("Dossier acquisition :", dossier)

temps_debut = time.time()

compteur_total = 0
dans_pic = False
debut_pic = None
pics = []
energie_totale = 0

dernier_temps = temps_debut

with open(fichier_mesures, "w", newline="") as f_mesures, open(fichier_pics, "w", newline="") as f_pics:

    writer_mesures = csv.writer(f_mesures)
    writer_pics = csv.writer(f_pics)

    writer_mesures.writerow([
        "temps_s",
        "numero_paquet",
        "taille_message",
        "message",
        "source",
        "rssi_dbm",
        "puissance_w",
        "energie_j"
    ])

    writer_pics.writerow([
        "numero_pic",
        "debut_s",
        "fin_s",
        "duree_s",
        "rssi_seuil_dbm"
    ])

    while True:
        data, addr = sock.recvfrom(4096)

        maintenant = time.time()
        temps_s = maintenant - temps_debut

        dt = maintenant - dernier_temps
        dernier_temps = maintenant

        message = data.decode(errors="ignore")
        rssi = lire_rssi()

        if rssi is not None:
            puissance_w = dbm_to_watt(rssi)
            energie = puissance_w * dt
            energie_totale += energie
        else:
            puissance_w = 0
            energie = 0

        compteur_total += 1

        print("-----------------------------------")
        print("Temps :", round(temps_s, 2), "s")
        print("Message reçu :", message)
        print("Source :", addr)
        print("RSSI :", rssi, "dBm")
        print("Puissance :", puissance_w, "W")
        print("Energie instantanée :", energie, "J")

        taille_message = len(message)

        writer_mesures.writerow([
            round(temps_s, 3),
            compteur_total,
            taille_message,
            message,
            str(addr),
            rssi,
            puissance_w,
            energie
        ])
        f_mesures.flush()

        if rssi is not None:

            if not dans_pic and rssi > SEUIL_RSSI:
                dans_pic = True
                debut_pic = temps_s
                print(">>> Début pic RSSI")

            elif dans_pic and rssi <= SEUIL_RSSI:
                fin_pic = temps_s
                duree_pic = fin_pic - debut_pic

                pics.append({
                    "debut": debut_pic,
                    "fin": fin_pic,
                    "duree": duree_pic
                })

                numero_pic = len(pics)

                writer_pics.writerow([
                    numero_pic,
                    round(debut_pic, 3),
                    round(fin_pic, 3),
                    round(duree_pic, 3),
                    SEUIL_RSSI
                ])
                f_pics.flush()

                print("<<< Fin pic RSSI")
                print("Durée du pic :", round(duree_pic, 3), "s")

                dans_pic = False
                debut_pic = None

        with open(fichier_rapport, "w") as f:
            f.write("RAPPORT UDP + RSSI WIFI\n")
            f.write("=======================\n\n")
            f.write(f"Date : {datetime.now()}\n")
            f.write(f"Dossier : {dossier}\n")
            f.write(f"Nombre total de paquets : {compteur_total}\n")
            f.write(f"Energie totale : {energie_totale:.3e} J\n")
            f.write(f"Nombre de pics RSSI : {len(pics)}\n")
            f.write(f"Seuil RSSI : {SEUIL_RSSI} dBm\n")
