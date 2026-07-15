from pathlib import Path

"""
ble_frame_driver.py

Capture des trames BLE avec un Adafruit Bluefruit LE Sniffer nRF51822
reconnu par Wireshark/TShark comme interface extcap.

Fonctions :
- détection automatique de l'interface nRF Sniffer ;
- capture des trames BLE avec TShark ;
- lecture du timestamp, de l'adresse, du RSSI, du canal, du type PDU
  et de la longueur BLE ;
- calcul de la durée radio d'une trame BLE sur le PHY LE 1M ;
- calcul du temps de fin estimé ;
- affichage dans le terminal ;
- sauvegarde CSV automatique.

Important :
La durée est calculée à partir de la longueur du champ BLE et du PHY LE 1M.
Ce n'est pas une mesure analogique à l'oscilloscope.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# Laisser None pour rechercher automatiquement l'interface dans `tshark -D`.
# Tu peux aussi imposer :
# NRF_INTERFACE = "/dev/ttyUSB0-4.4"
# ou :
# NRF_INTERFACE = "11"
NRF_INTERFACE: Optional[str] = None

DOSSIER_SORTIE = Path("acquisitions_ble")
FICHIER_CSV = DOSSIER_SORTIE / "trames_ble.csv"

# Le nRF51822 utilise le PHY Bluetooth LE 1M.
PHY_BLE = "LE 1M"

# Filtre d'affichage Wireshark.
FILTRE_BLE = "btle"


# ---------------------------------------------------------------------------
# STRUCTURE D'UNE TRAME
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BLEFrame:
    numero: int
    debut_s: float
    fin_s: float
    duree_us: float
    mac: str
    rssi_dbm: Optional[int]
    canal: Optional[int]
    longueur_payload_octets: int
    type_pdu: str
    phy: str


# ---------------------------------------------------------------------------
# CALCUL DE LA DURÉE RADIO
# ---------------------------------------------------------------------------

def calculer_duree_ble_us(longueur_payload_octets: int, phy: str = "LE 1M") -> float:
    """
    Calcule la durée radio d'une trame BLE.

    Pour une trame BLE legacy sur LE 1M :
        préambule           : 1 octet
        adresse d'accès     : 4 octets
        en-tête Link Layer  : 2 octets
        payload             : longueur donnée par btle.length
        CRC                 : 3 octets

    À 1 Mbit/s, un bit dure 1 microseconde.
    Donc :
        durée_us = (1 + 4 + 2 + payload + 3) * 8
                 = (10 + payload) * 8

    Remarque :
    `btle.length` correspond normalement au champ Length de l'en-tête BLE,
    donc à la longueur du payload Link Layer, sans les 2 octets d'en-tête.
    """
    if longueur_payload_octets < 0:
        raise ValueError("La longueur BLE doit être positive ou nulle.")

    if phy == "LE 1M":
        return float((10 + longueur_payload_octets) * 8)

    if phy == "LE 2M":
        # Préambule de 2 octets sur LE 2M.
        total_bits = (2 + 4 + 2 + longueur_payload_octets + 3) * 8
        return total_bits / 2.0

    raise ValueError(f"PHY non pris en charge : {phy}")


# ---------------------------------------------------------------------------
# OUTILS TSHARK
# ---------------------------------------------------------------------------

def verifier_tshark() -> None:
    if shutil.which("tshark") is None:
        raise RuntimeError(
            "TShark n'est pas installé. Installe-le avec : "
            "sudo apt install tshark"
        )


def lister_interfaces_tshark() -> str:
    resultat = subprocess.run(
        ["tshark", "-D"],
        capture_output=True,
        text=True,
        check=False,
    )

    if resultat.returncode != 0:
        raise RuntimeError(
            "Impossible de lister les interfaces TShark.\n"
            f"Erreur : {resultat.stderr.strip()}"
        )

    return resultat.stdout


def detecter_interface_nrf() -> str:
    """
    Recherche une interface contenant 'nRF Sniffer for Bluetooth LE'.

    Exemple attendu :
        11. /dev/ttyUSB0-4.4 (nRF Sniffer for Bluetooth LE)

    Retourne de préférence le nom technique :
        /dev/ttyUSB0-4.4
    """
    sortie = lister_interfaces_tshark()

    for ligne in sortie.splitlines():
        if "nRF Sniffer for Bluetooth LE" not in ligne:
            continue

        match = re.match(r"\s*\d+\.\s+(\S+)", ligne)
        if match:
            return match.group(1)

    raise RuntimeError(
        "Interface nRF Sniffer introuvable dans `tshark -D`.\n"
        "Vérifie que le dongle est branché et que le plugin extcap fonctionne."
    )


def champ_tshark_disponible(nom_champ: str) -> bool:
    """
    Vérifie qu'un champ Wireshark existe dans cette version de TShark.
    """
    resultat = subprocess.run(
        ["tshark", "-G", "fields"],
        capture_output=True,
        text=True,
        check=False,
    )

    if resultat.returncode != 0:
        return False

    return any(
        len(ligne.split("\t")) > 2 and ligne.split("\t")[2] == nom_champ
        for ligne in resultat.stdout.splitlines()
    )


# ---------------------------------------------------------------------------
# DRIVER BLE
# ---------------------------------------------------------------------------

class BLESnifferDriver:
    def __init__(
        self,
        interface: Optional[str] = None,
        fichier_csv: Path = FICHIER_CSV,
        callback: Optional[Callable[[BLEFrame], None]] = None,
    ) -> None:
        self.interface = interface or detecter_interface_nrf()
        self.fichier_csv = Path(fichier_csv)
        self.callback = callback

        self.processus: Optional[subprocess.Popen[str]] = None
        self.thread_lecture: Optional[threading.Thread] = None
        self.thread_erreur: Optional[threading.Thread] = None
        self.en_cours = False
        self.numero_trame = 0

        self.champ_rssi = self._choisir_champ(
            ["nordic_ble.rssi", "btcommon.eir_ad.entry.tx_power_level"],
            obligatoire=False,
        )
        self.champ_canal = self._choisir_champ(
            ["nordic_ble.channel"],
            obligatoire=False,
        )
        self.champ_adresse = self._choisir_champ(
            [
                "btle.advertising_address",
                "btle.master_bd_addr",
                "btle.slave_bd_addr",
            ],
            obligatoire=False,
        )
        self.champ_longueur = self._choisir_champ(
            ["btle.length"],
            obligatoire=True,
        )
        self.champ_type = self._choisir_champ(
            ["btle.advertising_header.pdu_type", "btle.data_header.llid"],
            obligatoire=False,
        )

    @staticmethod
    def _choisir_champ(candidats: list[str], obligatoire: bool) -> str:
        for champ in candidats:
            if champ_tshark_disponible(champ):
                return champ

        if obligatoire:
            raise RuntimeError(
                "Aucun des champs TShark obligatoires n'est disponible : "
                + ", ".join(candidats)
            )

        return ""

    def construire_commande(self) -> list[str]:
        champs = [
            "frame.time_epoch",
            self.champ_adresse,
            self.champ_longueur,
            self.champ_type,
            self.champ_rssi,
            self.champ_canal,
        ]

        commande = [
            "tshark",
            "-l",
            "-n",
            "-i",
            self.interface,
            "-Y",
            FILTRE_BLE,
            "-T",
            "fields",
            "-E",
            "separator=;",
            "-E",
            "occurrence=f",
            "-E",
            "quote=n",
        ]

        for champ in champs:
            if champ:
                commande.extend(["-e", champ])
            else:
                # TShark ne sait pas recevoir un champ vide.
                # On ajoute `frame.number` comme champ de remplacement,
                # puis on ignorera la valeur correspondante.
                commande.extend(["-e", "frame.number"])

        return commande

    def demarrer(self) -> None:
        if self.en_cours:
            return

        self.fichier_csv.parent.mkdir(parents=True, exist_ok=True)

        commande = self.construire_commande()

        print(f"Interface utilisée : {self.interface}")
        print("Commande TShark :")
        print(" ".join(commande))

        self.processus = subprocess.Popen(
            commande,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self.en_cours = True

        self.thread_lecture = threading.Thread(
            target=self._boucle_lecture,
            daemon=True,
        )
        self.thread_lecture.start()

        self.thread_erreur = threading.Thread(
            target=self._boucle_erreur,
            daemon=True,
        )
        self.thread_erreur.start()

    def arreter(self) -> None:
        self.en_cours = False

        if self.processus is None:
            return

        self.processus.terminate()

        try:
            self.processus.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.processus.kill()

        self.processus = None

    def _boucle_erreur(self) -> None:
        if self.processus is None or self.processus.stderr is None:
            return

        for ligne in self.processus.stderr:
            if not self.en_cours:
                break

            texte = ligne.strip()
            if texte:
                print(f"[TShark] {texte}", file=sys.stderr)

    def _boucle_lecture(self) -> None:
        if self.processus is None or self.processus.stdout is None:
            return

        nouveau_fichier = not self.fichier_csv.exists()

        with self.fichier_csv.open(
            "a",
            newline="",
            encoding="utf-8",
        ) as fichier:
            writer = csv.writer(fichier)

            if nouveau_fichier:
                writer.writerow([
                    "numero_trame",
                    "temps_debut_s",
                    "temps_fin_s",
                    "duree_us",
                    "mac",
                    "rssi_dbm",
                    "canal",
                    "longueur_payload_octets",
                    "type_pdu",
                    "phy",
                ])

            for ligne in self.processus.stdout:
                if not self.en_cours:
                    break

                trame = self._parser_ligne(ligne)

                if trame is None:
                    continue

                writer.writerow([
                    trame.numero,
                    f"{trame.debut_s:.9f}",
                    f"{trame.fin_s:.9f}",
                    f"{trame.duree_us:.3f}",
                    trame.mac,
                    "" if trame.rssi_dbm is None else trame.rssi_dbm,
                    "" if trame.canal is None else trame.canal,
                    trame.longueur_payload_octets,
                    trame.type_pdu,
                    trame.phy,
                ])
                fichier.flush()

                if self.callback is not None:
                    self.callback(trame)

    @staticmethod
    def _vers_entier_optionnel(texte: str) -> Optional[int]:
        texte = texte.strip()

        if not texte:
            return None

        match = re.search(r"-?\d+", texte)
        if not match:
            return None

        try:
            return int(match.group(0))
        except ValueError:
            return None

    def _parser_ligne(self, ligne: str) -> Optional[BLEFrame]:
        champs = ligne.rstrip("\n").split(";")

        while len(champs) < 6:
            champs.append("")

        debut_txt = champs[0].strip()
        adresse_txt = champs[1].strip()
        longueur_txt = champs[2].strip()
        type_txt = champs[3].strip()
        rssi_txt = champs[4].strip()
        canal_txt = champs[5].strip()

        if not debut_txt or not longueur_txt:
            return None

        try:
            debut_s = float(debut_txt)
            longueur_payload = int(longueur_txt)
        except ValueError:
            return None

        duree_us = calculer_duree_ble_us(
            longueur_payload_octets=longueur_payload,
            phy=PHY_BLE,
        )
        fin_s = debut_s + duree_us / 1_000_000.0

        self.numero_trame += 1

        return BLEFrame(
            numero=self.numero_trame,
            debut_s=debut_s,
            fin_s=fin_s,
            duree_us=duree_us,
            mac=adresse_txt or "inconnue",
            rssi_dbm=self._vers_entier_optionnel(rssi_txt),
            canal=self._vers_entier_optionnel(canal_txt),
            longueur_payload_octets=longueur_payload,
            type_pdu=type_txt or "inconnu",
            phy=PHY_BLE,
        )


# ---------------------------------------------------------------------------
# AFFICHAGE TERMINAL
# ---------------------------------------------------------------------------

def afficher_trame(trame: BLEFrame) -> None:
    rssi = "---" if trame.rssi_dbm is None else f"{trame.rssi_dbm} dBm"
    canal = "---" if trame.canal is None else str(trame.canal)

    print(
        f"Trame {trame.numero:06d} | "
        f"MAC={trame.mac} | "
        f"RSSI={rssi} | "
        f"canal={canal} | "
        f"payload={trame.longueur_payload_octets} octets | "
        f"durée={trame.duree_us:.1f} µs | "
        f"début={trame.debut_s:.9f} s | "
        f"fin={trame.fin_s:.9f} s"
    )


# ---------------------------------------------------------------------------
# PROGRAMME PRINCIPAL
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        verifier_tshark()

        interface = NRF_INTERFACE or detecter_interface_nrf()

        driver = BLESnifferDriver(
            interface=interface,
            fichier_csv=FICHIER_CSV,
            callback=afficher_trame,
        )

        driver.demarrer()

        print()
        print("Capture BLE démarrée.")
        print(f"CSV : {FICHIER_CSV}")
        print("Appuie sur Ctrl+C pour arrêter.")
        print()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nArrêt demandé par l'utilisateur.")

        try:
            driver.arreter()
        except UnboundLocalError:
            pass

        return 0

    except Exception as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

