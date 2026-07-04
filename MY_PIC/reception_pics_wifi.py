import socket
import time
import csv

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

# Seuil : si on reçoit plus que ce nombre de paquets/s, on considère qu'il y a un pic Wi-Fi
SEUIL_PAQUETS_PAR_SECONDE = 50

fichier_mesures = "mesures_pics_wifi.csv"
fichier_pics = "duree_pics_wifi.csv"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("Serveur UDP prêt")
print("En attente des messages Wi-Fi...")
print("Seuil de pic :", SEUIL_PAQUETS_PAR_SECONDE, "paquets/s")

temps_debut = time.time()
dernier_temps = temps_debut

compteur_total = 0
paquets_intervalle = 0

dans_pic = False
debut_pic = None
pics = []

with open(fichier_mesures, "w", newline="") as f_mesures, open(fichier_pics, "w", newline="") as f_pics:
    writer_mesures = csv.writer(f_mesures)
    writer_pics = csv.writer(f_pics)

    writer_mesures.writerow(["temps_s", "paquets_total", "paquets_par_seconde", "message", "source"])
    writer_pics.writerow(["numero_pic", "debut_s", "fin_s", "duree_s", "paquets_s_max"])

    pps_max_pic = 0

    while True:
        data, addr = sock.recvfrom(4096)

        compteur_total += 1
        paquets_intervalle += 1

        message = data.decode(errors="ignore")

        # Affichage du message reçu
        print("Message reçu de", addr, ":", message)

        maintenant = time.time()

        # Toutes les 1 seconde, on calcule le nombre de paquets/s
        if maintenant - dernier_temps >= 1.0:
            temps_s = maintenant - temps_debut
            dt = maintenant - dernier_temps

            paquets_s = paquets_intervalle / dt

            print("--------------------------------")
            print("Temps :", round(temps_s, 2), "s")
            print("Paquets/s :", round(paquets_s, 2))
            print("Total paquets :", compteur_total)

            writer_mesures.writerow([
                round(temps_s, 2),
                compteur_total,
                round(paquets_s, 2),
                message,
                str(addr)
            ])
            f_mesures.flush()

            # Détection début de pic
            if not dans_pic and paquets_s >= SEUIL_PAQUETS_PAR_SECONDE:
                dans_pic = True
                debut_pic = temps_s
                pps_max_pic = paquets_s
                print(">>> Début pic Wi-Fi")

            # Pendant le pic
            elif dans_pic and paquets_s >= SEUIL_PAQUETS_PAR_SECONDE:
                if paquets_s > pps_max_pic:
                    pps_max_pic = paquets_s

            # Fin de pic
            elif dans_pic and paquets_s < SEUIL_PAQUETS_PAR_SECONDE:
                fin_pic = temps_s
                duree_pic = fin_pic - debut_pic

                pics.append({
                    "debut": debut_pic,
                    "fin": fin_pic,
                    "duree": duree_pic,
                    "pps_max": pps_max_pic
                })

                numero = len(pics)

                writer_pics.writerow([
                    numero,
                    round(debut_pic, 2),
                    round(fin_pic, 2),
                    round(duree_pic, 2),
                    round(pps_max_pic, 2)
                ])
                f_pics.flush()

                print("<<< Fin pic Wi-Fi")
                print("Durée du pic :", round(duree_pic, 2), "s")

                dans_pic = False
                debut_pic = None
                pps_max_pic = 0

            paquets_intervalle = 0
            dernier_temps = maintenant
