import socket
import time
import csv

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

fichier_csv = "mesure_wifi.csv"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("Serveur UDP prêt sur le port", UDP_PORT)
print("Enregistrement dans", fichier_csv)

compteur = 0
t0 = time.time()
dernier_temps = t0
paquets_intervalle = 0

with open(fichier_csv, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["temps_s", "paquets_total", "paquets_par_seconde", "debit_ko_s", "source"])

    while True:
        data, addr = sock.recvfrom(4096)
        compteur += 1
        paquets_intervalle += 1

        maintenant = time.time()

        if maintenant - dernier_temps >= 1.0:
            dt_total = maintenant - t0
            dt_intervalle = maintenant - dernier_temps

            pps = paquets_intervalle / dt_intervalle
            debit_ko_s = (paquets_intervalle * len(data)) / dt_intervalle / 1024

            writer.writerow([
                round(dt_total, 2),
                compteur,
                round(pps, 2),
                round(debit_ko_s, 2),
                str(addr)
            ])
            f.flush()

            print("Temps:", round(dt_total, 2), "s | Paquets/s:", round(pps, 2), "| Débit:", round(debit_ko_s, 2), "ko/s")

            paquets_intervalle = 0
            dernier_temps = maintenant
