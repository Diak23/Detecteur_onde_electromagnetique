#!/usr/bin/env python3
"""
ble_frame_driver_corrige_v2.py

Driver BLE corrigé pour :
- Raspberry Pi ;
- Adafruit Bluefruit LE Sniffer nRF51822 ;
- Wireshark/TShark extcap.

Corrections principales :
1. La MAC est recherchée dans plusieurs champs TShark pour CHAQUE trame.
2. Les trames sans adresse MAC valide peuvent être ignorées.
3. Les paquets explicitement marqués avec un CRC incorrect sont exclus.
4. La longueur BLE utilise plusieurs champs de repli.
5. Le fichier reste compatible avec interface_ble_v5.py.

Place ce fichier dans ~/acquisition_udp puis renomme-le en :
    ble_frame_driver_corrige.py
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


# =============================================================================
# CONFIGURATION
# =============================================================================

NRF_INTERFACE: Optional[str] = None

DOSSIER_SORTIE = Path("acquisitions_ble")
FICHIER_CSV = DOSSIER_SORTIE / "trames_ble.csv"

PHY_BLE = "LE 1M"

# Exclut les paquets dont Wireshark signale explicitement un CRC incorrect.
FILTRE_BLE = "btle and not btle.crc.incorrect"

# Recommandé pour le filtrage par appareil dans l'interface.
# Les trames sans adresse exploitable ne seront pas transmises à l'interface.
IGNORER_TRAMES_SANS_MAC = True

MAC_INCONNUE = "inconnue"

REGEX_MAC = re.compile(
    r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
)


# =============================================================================
# STRUCTURE D'UNE TRAME
# =============================================================================

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


# =============================================================================
# CALCUL DE DURÉE
# =============================================================================

def calculer_duree_ble_us(
    longueur_payload_octets: int,
    phy: str = "LE 1M",
) -> float:
    """
    Durée d'une trame BLE legacy.

    LE 1M :
      préambule         1 octet
      access address    4 octets
      en-tête LL        2 octets
      payload           N octets
      CRC               3 octets

      durée = (10 + N) × 8 µs
    """
    if longueur_payload_octets < 0:
        raise ValueError(
            "La longueur BLE doit être positive ou nulle."
        )

    if phy == "LE 1M":
        return float((10 + longueur_payload_octets) * 8)

    if phy == "LE 2M":
        total_bits = (
            2 + 4 + 2 + longueur_payload_octets + 3
        ) * 8
        return total_bits / 2.0

    raise ValueError(f"PHY non pris en charge : {phy}")


# =============================================================================
# OUTILS TSHARK
# =============================================================================

def verifier_tshark() -> None:
    if shutil.which("tshark") is None:
        raise RuntimeError(
            "TShark n'est pas installé. "
            "Lance : sudo apt install tshark"
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
            "Impossible de lister les interfaces TShark : "
            f"{resultat.stderr.strip()}"
        )

    return resultat.stdout


def detecter_interface_nrf() -> str:
    sortie = lister_interfaces_tshark()

    for ligne in sortie.splitlines():
        if "nRF Sniffer for Bluetooth LE" not in ligne:
            continue

        match = re.match(r"\s*\d+\.\s+(\S+)", ligne)

        if match:
            return match.group(1)

    raise RuntimeError(
        "Interface nRF Sniffer introuvable dans `tshark -D`."
    )


def champs_tshark() -> set[str]:
    resultat = subprocess.run(
        ["tshark", "-G", "fields"],
        capture_output=True,
        text=True,
        check=False,
    )

    disponibles: set[str] = set()

    if resultat.returncode != 0:
        return disponibles

    for ligne in resultat.stdout.splitlines():
        morceaux = ligne.split("\t")

        if len(morceaux) > 2:
            disponibles.add(morceaux[2])

    return disponibles


# =============================================================================
# DRIVER
# =============================================================================

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
        self.nb_sans_mac_ignorees = 0

        disponibles = champs_tshark()

        # On conserve TOUS les champs disponibles.
        # La sélection de la première valeur non vide se fait ensuite
        # pour chaque paquet, contrairement à l'ancienne version.
        self.champs_adresse = self._champs_disponibles(
            disponibles,
            [
                "btle.advertising_address",
                "bluetooth.src",
                "bluetooth.dst",
                "btle.master_bd_addr",
                "btle.slave_bd_addr",
            ],
        )

        self.champs_longueur = self._champs_disponibles(
            disponibles,
            [
                "btle.advertising_header.length",
                "btle.data_header.length",
                "btle.length",
            ],
        )

        self.champs_type = self._champs_disponibles(
            disponibles,
            [
                "btle.advertising_header.pdu_type",
                "btle.data_header.llid",
            ],
        )

        self.champs_rssi = self._champs_disponibles(
            disponibles,
            ["nordic_ble.rssi"],
        )

        self.champs_canal = self._champs_disponibles(
            disponibles,
            ["nordic_ble.channel"],
        )

        if not self.champs_longueur:
            raise RuntimeError(
                "Aucun champ de longueur BLE compatible n'est "
                "disponible dans TShark."
            )

        # Liste ordonnée des colonnes réellement envoyées à TShark.
        self.colonnes: list[tuple[str, str]] = [
            ("timestamp", "frame.time_epoch"),
        ]

        for champ in self.champs_adresse:
            self.colonnes.append(("adresse", champ))

        for champ in self.champs_longueur:
            self.colonnes.append(("longueur", champ))

        for champ in self.champs_type:
            self.colonnes.append(("type", champ))

        for champ in self.champs_rssi:
            self.colonnes.append(("rssi", champ))

        for champ in self.champs_canal:
            self.colonnes.append(("canal", champ))

    @staticmethod
    def _champs_disponibles(
        disponibles: set[str],
        candidats: list[str],
    ) -> list[str]:
        return [
            champ
            for champ in candidats
            if champ in disponibles
        ]

    def construire_commande(self) -> list[str]:
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

        for _, champ in self.colonnes:
            commande.extend(["-e", champ])

        return commande

    def demarrer(self) -> None:
        if self.en_cours:
            return

        self.fichier_csv.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        commande = self.construire_commande()

        print(f"Interface utilisée : {self.interface}")
        print("Champs MAC utilisés :")
        for champ in self.champs_adresse:
            print(f"  - {champ}")

        print("Champs longueur utilisés :")
        for champ in self.champs_longueur:
            print(f"  - {champ}")

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

        if self.nb_sans_mac_ignorees:
            print(
                "Trames sans MAC ignorées : "
                f"{self.nb_sans_mac_ignorees}"
            )

    def _boucle_erreur(self) -> None:
        if (
            self.processus is None
            or self.processus.stderr is None
        ):
            return

        for ligne in self.processus.stderr:
            if not self.en_cours:
                break

            texte = ligne.strip()

            if texte:
                print(f"[TShark] {texte}", file=sys.stderr)

    def _boucle_lecture(self) -> None:
        if (
            self.processus is None
            or self.processus.stdout is None
        ):
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
                    (
                        ""
                        if trame.rssi_dbm is None
                        else trame.rssi_dbm
                    ),
                    (
                        ""
                        if trame.canal is None
                        else trame.canal
                    ),
                    trame.longueur_payload_octets,
                    trame.type_pdu,
                    trame.phy,
                ])
                fichier.flush()

                if self.callback is not None:
                    self.callback(trame)

    @staticmethod
    def _premiere_valeur(
        valeurs: list[str],
    ) -> str:
        for valeur in valeurs:
            valeur = valeur.strip()

            if valeur:
                return valeur

        return ""

    @staticmethod
    def _premiere_mac_valide(
        valeurs: list[str],
    ) -> str:
        for valeur in valeurs:
            valeur = valeur.strip().lower()

            # Certains champs peuvent retourner plusieurs valeurs.
            for morceau in re.split(r"[, ]+", valeur):
                if REGEX_MAC.fullmatch(morceau):
                    return morceau

        return MAC_INCONNUE

    @staticmethod
    def _vers_entier_optionnel(
        texte: str,
    ) -> Optional[int]:
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

    @staticmethod
    def _premier_entier(
        valeurs: list[str],
    ) -> Optional[int]:
        for valeur in valeurs:
            resultat = BLESnifferDriver._vers_entier_optionnel(
                valeur
            )

            if resultat is not None:
                return resultat

        return None

    def _parser_ligne(
        self,
        ligne: str,
    ) -> Optional[BLEFrame]:
        valeurs = ligne.rstrip("\n").split(";")

        while len(valeurs) < len(self.colonnes):
            valeurs.append("")

        groupes: dict[str, list[str]] = {
            "timestamp": [],
            "adresse": [],
            "longueur": [],
            "type": [],
            "rssi": [],
            "canal": [],
        }

        for (
            categorie,
            _champ,
        ), valeur in zip(self.colonnes, valeurs):
            groupes[categorie].append(valeur.strip())

        debut_txt = self._premiere_valeur(
            groupes["timestamp"]
        )

        if not debut_txt:
            return None

        try:
            debut_s = float(debut_txt)
        except ValueError:
            return None

        longueur_payload = self._premier_entier(
            groupes["longueur"]
        )

        if longueur_payload is None:
            return None

        # Sécurité contre les longueurs manifestement invalides.
        if longueur_payload < 0 or longueur_payload > 255:
            return None

        mac = self._premiere_mac_valide(
            groupes["adresse"]
        )

        if (
            IGNORER_TRAMES_SANS_MAC
            and mac == MAC_INCONNUE
        ):
            self.nb_sans_mac_ignorees += 1
            return None

        type_pdu = self._premiere_valeur(
            groupes["type"]
        ) or "inconnu"

        rssi = self._premier_entier(
            groupes["rssi"]
        )
        canal = self._premier_entier(
            groupes["canal"]
        )

        duree_us = calculer_duree_ble_us(
            longueur_payload,
            PHY_BLE,
        )
        fin_s = debut_s + duree_us / 1_000_000.0

        self.numero_trame += 1

        return BLEFrame(
            numero=self.numero_trame,
            debut_s=debut_s,
            fin_s=fin_s,
            duree_us=duree_us,
            mac=mac,
            rssi_dbm=rssi,
            canal=canal,
            longueur_payload_octets=longueur_payload,
            type_pdu=type_pdu,
            phy=PHY_BLE,
        )


# =============================================================================
# TEST TERMINAL
# =============================================================================

def afficher_trame(trame: BLEFrame) -> None:
    rssi = (
        "---"
        if trame.rssi_dbm is None
        else f"{trame.rssi_dbm} dBm"
    )
    canal = (
        "---"
        if trame.canal is None
        else str(trame.canal)
    )

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


def main() -> int:
    driver: Optional[BLESnifferDriver] = None

    try:
        verifier_tshark()

        interface = (
            NRF_INTERFACE
            or detecter_interface_nrf()
        )

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
        return 0

    except Exception as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 1

    finally:
        if driver is not None:
            driver.arreter()


if __name__ == "__main__":
    raise SystemExit(main())
