import socket
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("Serveur UDP prêt sur le port", UDP_PORT)
print("En attente des paquets Wi-Fi...")

compteur = 0
t0 = time.time()

while True:
    data, addr = sock.recvfrom(4096)
    compteur += 1

    if compteur % 100 == 0:
        dt = time.time() - t0
        debit_paquets = compteur / dt
        debit_octets = (compteur * len(data)) / dt

        print("Paquets reçus :", compteur)
        print("Débit paquets :", round(debit_paquets, 2), "paquets/s")
        print("Débit approx. :", round(debit_octets / 1024, 2), "ko/s")
        print("Dernière source :", addr)
        print("-----------------------------")
