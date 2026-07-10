import tkinter as tk
from tkinter import ttk
import subprocess
import re
import time
import socket
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

running = False
mode_selectionne = "Wi-Fi 2.4 GHz"
t0 = None

temps = []
rssi_values = []
puissance_values = []
snr_values = []

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
    except:
        return None
    return None


try:
    from LoRaRF import SX127x
    LORA_DISPONIBLE = True
except:
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


def changer_mode(event=None):
    global mode_selectionne

    mode_selectionne = combo_mode.get()
    label_mode.config(text=f"Mode sélectionné : {mode_selectionne}")
    reinitialiser_graphes()

    if mode_selectionne == "LoRa 868 MHz":
        initialiser_lora()


def demarrer():
    global running, t0

    running = True
    t0 = time.time()
    temps.clear()
    rssi_values.clear()
    puissance_values.clear()
    snr_values.clear()

    label_etat.config(text="Acquisition démarrée")
    boucle_acquisition()


def arreter():
    global running
    running = False
    label_etat.config(text="Acquisition arrêtée")


def reinitialiser_graphes():
    temps.clear()
    rssi_values.clear()
    puissance_values.clear()
    snr_values.clear()

    ax1.clear()
    ax2.clear()

    ax1.set_title("RSSI")
    ax1.set_xlabel("Temps (s)")
    ax1.set_ylabel("RSSI (dBm)")
    ax1.grid(True)

    ax2.set_title("Puissance reçue")
    ax2.set_xlabel("Temps (s)")
    ax2.set_ylabel("Puissance (W)")
    ax2.grid(True)

    canvas.draw()


def mettre_a_jour_graphes():
    ax1.clear()
    ax2.clear()

    ax1.plot(temps, rssi_values, label="RSSI")
    ax1.set_title(f"RSSI - {mode_selectionne}")
    ax1.set_xlabel("Temps (s)")
    ax1.set_ylabel("RSSI (dBm)")
    ax1.grid(True)
    ax1.legend()

    ax2.plot(temps, puissance_values, label="Puissance")
    ax2.set_title(f"Puissance reçue - {mode_selectionne}")
    ax2.set_xlabel("Temps (s)")
    ax2.set_ylabel("Puissance (W)")
    ax2.grid(True)
    ax2.legend()

    fig.subplots_adjust(hspace=0.55)
    canvas.draw()


def ajouter_mesure(rssi, snr=None):
    t = time.time() - t0
    puissance = dbm_to_watt(rssi)

    temps.append(t)
    rssi_values.append(rssi)
    puissance_values.append(puissance)

    if snr is not None:
        snr_values.append(snr)

    label_rssi.config(text=f"RSSI : {rssi} dBm")
    label_puissance.config(text=f"Puissance : {puissance:.3e} W")

    mettre_a_jour_graphes()


def boucle_acquisition():
    if not running:
        return

    if mode_selectionne == "Wi-Fi 2.4 GHz":
        try:
            data, addr = sock.recvfrom(4096)
            message = data.decode(errors="ignore")
            rssi = lire_rssi_wifi()

            if rssi is not None:
                label_message.config(text=f"Message UDP : {message[:60]}")
                label_source.config(text=f"Source : {addr}")
                label_snr.config(text="SNR : non utilisé en Wi-Fi")

                ajouter_mesure(rssi)

        except BlockingIOError:
            pass

    elif mode_selectionne == "LoRa 868 MHz":
        message, rssi, snr = lire_lora()

        if message is not None and rssi is not None:
            label_message.config(text=f"Message LoRa : {message[:60]}")
            label_source.config(text="Source : module LoRa")
            label_snr.config(text=f"SNR LoRa : {snr} dB")

            ajouter_mesure(rssi, snr)

    fenetre.after(100, boucle_acquisition)


fenetre = tk.Tk()
fenetre.title("Détecteur Wi-Fi 2.4 GHz / LoRa 868 MHz")
fenetre.geometry("1200x750")

main = ttk.Frame(fenetre, padding=10)
main.pack(fill="both", expand=True)

frame_gauche = ttk.Frame(main)
frame_gauche.pack(side="left", fill="y", padx=5)

frame_choix = ttk.LabelFrame(frame_gauche, text="Sélection du réseau", padding=10)
frame_choix.pack(fill="x", pady=5)

combo_mode = ttk.Combobox(
    frame_choix,
    values=["Wi-Fi 2.4 GHz", "LoRa 868 MHz"],
    state="readonly"
)
combo_mode.current(0)
combo_mode.pack(fill="x", padx=5, pady=5)
combo_mode.bind("<<ComboboxSelected>>", changer_mode)

label_mode = ttk.Label(frame_choix, text="Mode sélectionné : Wi-Fi 2.4 GHz")
label_mode.pack(anchor="w")

frame_boutons = ttk.LabelFrame(frame_gauche, text="Contrôles", padding=10)
frame_boutons.pack(fill="x", pady=5)

ttk.Button(frame_boutons, text="Démarrer", command=demarrer).pack(fill="x", pady=2)
ttk.Button(frame_boutons, text="Arrêter", command=arreter).pack(fill="x", pady=2)
ttk.Button(frame_boutons, text="Réinitialiser graphes", command=reinitialiser_graphes).pack(fill="x", pady=2)

frame_mesures = ttk.LabelFrame(frame_gauche, text="Mesures temps réel", padding=10)
frame_mesures.pack(fill="x", pady=5)

label_etat = ttk.Label(frame_mesures, text="En attente")
label_etat.pack(anchor="w")

label_message = ttk.Label(frame_mesures, text="Message : ---")
label_message.pack(anchor="w")

label_source = ttk.Label(frame_mesures, text="Source : ---")
label_source.pack(anchor="w")

label_rssi = ttk.Label(frame_mesures, text="RSSI : ---")
label_rssi.pack(anchor="w")

label_puissance = ttk.Label(frame_mesures, text="Puissance : ---")
label_puissance.pack(anchor="w")

label_snr = ttk.Label(frame_mesures, text="SNR : ---")
label_snr.pack(anchor="w")

frame_graphe = ttk.Frame(main, padding=10)
frame_graphe.pack(side="right", fill="both", expand=True)

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7))
fig.subplots_adjust(hspace=0.55)

canvas = FigureCanvasTkAgg(fig, master=frame_graphe)
canvas.get_tk_widget().pack(fill="both", expand=True)

reinitialiser_graphes()

fenetre.mainloop()
