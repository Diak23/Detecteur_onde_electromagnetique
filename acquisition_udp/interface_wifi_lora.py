import tkinter as tk
from tkinter import ttk
import subprocess
import re
import time
import socket

# =========================
# PARAMÈTRES
# =========================

UDP_IP = "0.0.0.0"
UDP_PORT = 5005

running = False
mode_selectionne = "Wi-Fi 2.4 GHz"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)

# =========================
# OUTILS WIFI
# =========================

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

# =========================
# OUTILS LORA
# =========================

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

# =========================
# COMMANDES INTERFACE
# =========================

def changer_mode(event=None):
    global mode_selectionne

    mode_selectionne = combo_mode.get()
    label_mode.config(text=f"Mode sélectionné : {mode_selectionne}")

    if mode_selectionne == "LoRa 868 MHz":
        initialiser_lora()

def demarrer():
    global running
    running = True
    label_etat.config(text="Acquisition démarrée")
    boucle_acquisition()

def arreter():
    global running
    running = False
    label_etat.config(text="Acquisition arrêtée")

def boucle_acquisition():
    if not running:
        return

    if mode_selectionne == "Wi-Fi 2.4 GHz":
        try:
            data, addr = sock.recvfrom(4096)
            message = data.decode(errors="ignore")

            rssi = lire_rssi_wifi()

            if rssi is not None:
                puissance = dbm_to_watt(rssi)

                label_message.config(text=f"Message UDP : {message[:60]}")
                label_source.config(text=f"Source : {addr}")
                label_rssi.config(text=f"RSSI Wi-Fi : {rssi} dBm")
                label_puissance.config(text=f"Puissance : {puissance:.3e} W")
                label_snr.config(text="SNR : non utilisé en Wi-Fi")
            else:
                label_rssi.config(text="RSSI Wi-Fi : non disponible")

        except BlockingIOError:
            pass

    elif mode_selectionne == "LoRa 868 MHz":
        message, rssi, snr = lire_lora()

        if message is not None:
            label_message.config(text=f"Message LoRa : {message[:60]}")
            label_source.config(text="Source : module LoRa SX1276/RFM95")
            label_rssi.config(text=f"RSSI LoRa : {rssi} dBm")
            label_snr.config(text=f"SNR LoRa : {snr} dB")

            if rssi is not None:
                puissance = dbm_to_watt(rssi)
                label_puissance.config(text=f"Puissance estimée : {puissance:.3e} W")

    fenetre.after(100, boucle_acquisition)

# =========================
# INTERFACE TKINTER
# =========================

fenetre = tk.Tk()
fenetre.title("Détecteur Wi-Fi 2.4 GHz / LoRa 868 MHz")
fenetre.geometry("800x400")

main = ttk.Frame(fenetre, padding=15)
main.pack(fill="both", expand=True)

frame_choix = ttk.LabelFrame(main, text="Sélection du réseau", padding=10)
frame_choix.pack(fill="x", pady=5)

combo_mode = ttk.Combobox(
    frame_choix,
    values=["Wi-Fi 2.4 GHz", "LoRa 868 MHz"],
    state="readonly"
)
combo_mode.current(0)
combo_mode.pack(side="left", padx=5)
combo_mode.bind("<<ComboboxSelected>>", changer_mode)

label_mode = ttk.Label(frame_choix, text="Mode sélectionné : Wi-Fi 2.4 GHz")
label_mode.pack(side="left", padx=15)

frame_boutons = ttk.LabelFrame(main, text="Contrôles", padding=10)
frame_boutons.pack(fill="x", pady=5)

ttk.Button(frame_boutons, text="Démarrer", command=demarrer).pack(side="left", padx=5)
ttk.Button(frame_boutons, text="Arrêter", command=arreter).pack(side="left", padx=5)

frame_mesures = ttk.LabelFrame(main, text="Mesures temps réel", padding=10)
frame_mesures.pack(fill="both", expand=True, pady=5)

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

fenetre.mainloop()
