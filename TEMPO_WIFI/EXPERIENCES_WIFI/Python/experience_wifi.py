import socket
import time
import csv

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

fichier_csv = "experience_wifi.csv"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("Serveur UDP experimental pret")
print("Port :", UDP_PORT)
print("Fichier :", fichier_csv)

compteur_total = 0
paquets_intervalle = 0
octets_intervalle = 0

t0 = time.time()
dernier_temps = t0

with open(fichier_csv, "w", newline="") as f:
    writer = csv.writer(f)

    writer.writerow([
        "temps_s",
        "experience",
        "paquets_total",
        "paquets_par_seconde",
        "debit_ko_s",
        "delay_ms",
        "taille_message",
        "source"
    ])

    while True:
        data, addr = sock.recvfrom(4096)

        compteur_total += 1
        paquets_intervalle += 1
        octets_intervalle += len(data)

        message = data.decode(errors="ignore")
        morceaux = message.split(",", 4)

        if len(morceaux) >= 4:
            experience = morceaux[0]
            delay_ms = morceaux[2]
            taille_message = morceaux[3]
        else:
            experience = "inconnue"
            delay_ms = "inconnu"
            taille_message = "inconnu"

        maintenant = time.time()

        if maintenant - dernier_temps >= 1.0:
            dt_total = maintenant - t0
            dt_intervalle = maintenant - dernier_temps

            pps = paquets_intervalle / dt_intervalle
            debit_ko_s = octets_intervalle / dt_intervalle / 1024

            writer.writerow([
                round(dt_total, 2),
                experience,
                compteur_total,
                round(pps, 2),
                round(debit_ko_s, 2),
                delay_ms,
                taille_message,
                str(addr)
            ])

            f.flush()

            print(
                "Temps:", round(dt_total, 2), "s |",
                "Exp:", experience, "|",
                "Paquets/s:", round(pps, 2), "|",
                "Debit:", round(debit_ko_s, 2), "ko/s |",
                "Delay:", delay_ms, "ms |",
                "Taille:", taille_message
            )

            paquets_intervalle = 0
            octets_intervalle = 0
            dernier_temps = maintenant
