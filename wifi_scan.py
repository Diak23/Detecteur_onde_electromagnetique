import os

scan = os.popen("sudo iw wlan0 scan").read()

reseaux ={}
signal= None

for ligne in scan.splitlines():

    ligne = ligne.strip()

    if ligne.startswith("signal:"):
        signal = float(ligne.split()[1])

    if ligne.startswith("SSID:"):
       ssid = ligne.replace("SSID:", "").strip()

       if ssid != "" and signal is not None: 

          if ssid not in reseaux: 

             reseaux[ssid] = signal


          else:

              if signal > reseaux[ssid]:

                 reseaux[ssid]= signal


print("\n==== Réseaux WIFI détectés ====")

print("{:<35} {}".format("SSID", "Signal"))

print("-" *50)

for ssid, signal in reseaux.items():

    print("{:<35} {} dBm".format(ssid,signal))
