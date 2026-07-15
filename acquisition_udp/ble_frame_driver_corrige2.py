#!/usr/bin/env python3
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

NRF_INTERFACE: Optional[str] = None
DOSSIER_SORTIE = Path('acquisitions_ble')
FICHIER_CSV = DOSSIER_SORTIE / 'trames_ble.csv'
PHY_BLE = 'LE 1M'
FILTRE_BLE = 'btle'


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


def calculer_duree_ble_us(longueur_payload_octets: int, phy: str = 'LE 1M') -> float:
    if longueur_payload_octets < 0:
        raise ValueError('La longueur BLE doit être positive ou nulle.')

    if phy == 'LE 1M':
        return float((10 + longueur_payload_octets) * 8)

    if phy == 'LE 2M':
        total_bits = (2 + 4 + 2 + longueur_payload_octets + 3) * 8
        return total_bits / 2.0

    raise ValueError(f'PHY non pris en charge : {phy}')


def verifier_tshark() -> None:
    if shutil.which('tshark') is None:
        raise RuntimeError("TShark n'est pas installé. Lance : sudo apt install tshark")


def lister_interfaces_tshark() -> str:
    resultat = subprocess.run(['tshark', '-D'], capture_output=True, text=True, check=False)
    if resultat.returncode != 0:
        raise RuntimeError(f"Impossible de lister les interfaces TShark : {resultat.stderr.strip()}")
    return resultat.stdout


def detecter_interface_nrf() -> str:
    sortie = lister_interfaces_tshark()
    for ligne in sortie.splitlines():
        if 'nRF Sniffer for Bluetooth LE' in ligne:
            match = re.match(r'\s*\d+\.\s+(\S+)', ligne)
            if match:
                return match.group(1)
    raise RuntimeError("Interface nRF Sniffer introuvable dans `tshark -D`.")


def champs_tshark() -> set[str]:
    resultat = subprocess.run(['tshark', '-G', 'fields'], capture_output=True, text=True, check=False)
    disponibles: set[str] = set()
    if resultat.returncode != 0:
        return disponibles
    for ligne in resultat.stdout.splitlines():
        morceaux = ligne.split('\t')
        if len(morceaux) > 2:
            disponibles.add(morceaux[2])
    return disponibles


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

        disponibles = champs_tshark()
        self.champ_adresse = self._premier_disponible(
            disponibles,
            ['btle.advertising_address', 'btle.master_bd_addr', 'btle.slave_bd_addr'],
        )
        self.champ_longueur = self._premier_disponible(disponibles, ['btle.length'])
        self.champ_type = self._premier_disponible(
            disponibles,
            ['btle.advertising_header.pdu_type', 'btle.data_header.llid'],
        )
        self.champ_rssi = self._premier_disponible(disponibles, ['nordic_ble.rssi'])
        self.champ_canal = self._premier_disponible(disponibles, ['nordic_ble.channel'])

        if not self.champ_longueur:
            raise RuntimeError("Le champ obligatoire `btle.length` n'est pas disponible dans TShark.")

    @staticmethod
    def _premier_disponible(disponibles: set[str], candidats: list[str]) -> str:
        for champ in candidats:
            if champ in disponibles:
                return champ
        return ''

    def construire_commande(self) -> list[str]:
        champs = [
            'frame.time_epoch',
            self.champ_adresse,
            self.champ_longueur,
            self.champ_type,
            self.champ_rssi,
            self.champ_canal,
        ]

        commande = [
            'tshark', '-l', '-n', '-i', self.interface,
            '-Y', FILTRE_BLE,
            '-T', 'fields',
            '-E', 'separator=;',
            '-E', 'occurrence=f',
            '-E', 'quote=n',
        ]

        for champ in champs:
            commande.extend(['-e', champ if champ else 'frame.number'])

        return commande

    def demarrer(self) -> None:
        if self.en_cours:
            return

        self.fichier_csv.parent.mkdir(parents=True, exist_ok=True)
        commande = self.construire_commande()

        print(f'Interface utilisée : {self.interface}')
        print('Commande TShark :')
        print(' '.join(commande))

        self.processus = subprocess.Popen(
            commande,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.en_cours = True

        self.thread_lecture = threading.Thread(target=self._boucle_lecture, daemon=True)
        self.thread_lecture.start()

        self.thread_erreur = threading.Thread(target=self._boucle_erreur, daemon=True)
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
                print(f'[TShark] {texte}', file=sys.stderr)

    def _boucle_lecture(self) -> None:
        if self.processus is None or self.processus.stdout is None:
            return

        nouveau_fichier = not self.fichier_csv.exists()
        with self.fichier_csv.open('a', newline='', encoding='utf-8') as fichier:
            writer = csv.writer(fichier)

            if nouveau_fichier:
                writer.writerow([
                    'numero_trame', 'temps_debut_s', 'temps_fin_s', 'duree_us',
                    'mac', 'rssi_dbm', 'canal', 'longueur_payload_octets',
                    'type_pdu', 'phy',
                ])

            for ligne in self.processus.stdout:
                if not self.en_cours:
                    break

                trame = self._parser_ligne(ligne)
                if trame is None:
                    continue

                writer.writerow([
                    trame.numero,
                    f'{trame.debut_s:.9f}',
                    f'{trame.fin_s:.9f}',
                    f'{trame.duree_us:.3f}',
                    trame.mac,
                    '' if trame.rssi_dbm is None else trame.rssi_dbm,
                    '' if trame.canal is None else trame.canal,
                    trame.longueur_payload_octets,
                    trame.type_pdu,
                    trame.phy,
                ])
                fichier.flush()

                if self.callback is not None:
                    self.callback(trame)

    @staticmethod
    def _vers_entier_optionnel(texte: str) -> Optional[int]:
        match = re.search(r'-?\d+', texte.strip())
        return int(match.group(0)) if match else None

    def _parser_ligne(self, ligne: str) -> Optional[BLEFrame]:
        champs = ligne.rstrip('\n').split(';')
        while len(champs) < 6:
            champs.append('')

        debut_txt, adresse_txt, longueur_txt, type_txt, rssi_txt, canal_txt = [
            valeur.strip() for valeur in champs[:6]
        ]

        if not debut_txt or not longueur_txt:
            return None

        try:
            debut_s = float(debut_txt)
            longueur_payload = int(longueur_txt)
        except ValueError:
            return None

        duree_us = calculer_duree_ble_us(longueur_payload, PHY_BLE)
        fin_s = debut_s + duree_us / 1_000_000.0
        self.numero_trame += 1

        return BLEFrame(
            numero=self.numero_trame,
            debut_s=debut_s,
            fin_s=fin_s,
            duree_us=duree_us,
            mac=adresse_txt or 'inconnue',
            rssi_dbm=self._vers_entier_optionnel(rssi_txt),
            canal=self._vers_entier_optionnel(canal_txt),
            longueur_payload_octets=longueur_payload,
            type_pdu=type_txt or 'inconnu',
            phy=PHY_BLE,
        )


def afficher_trame(trame: BLEFrame) -> None:
    rssi = '---' if trame.rssi_dbm is None else f'{trame.rssi_dbm} dBm'
    canal = '---' if trame.canal is None else str(trame.canal)
    print(
        f'Trame {trame.numero:06d} | MAC={trame.mac} | RSSI={rssi} | '
        f'canal={canal} | payload={trame.longueur_payload_octets} octets | '
        f'durée={trame.duree_us:.1f} µs | début={trame.debut_s:.9f} s | '
        f'fin={trame.fin_s:.9f} s'
    )


def main() -> int:
    driver: Optional[BLESnifferDriver] = None

    try:
        verifier_tshark()
        interface = NRF_INTERFACE or detecter_interface_nrf()
        driver = BLESnifferDriver(
            interface=interface,
            fichier_csv=FICHIER_CSV,
            callback=afficher_trame,
        )
        driver.demarrer()

        print('\nCapture BLE démarrée.')
        print(f'CSV : {FICHIER_CSV}')
        print('Appuie sur Ctrl+C pour arrêter.\n')

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nArrêt demandé par l'utilisateur.")
        return 0

    except Exception as erreur:
        print(f'Erreur : {erreur}', file=sys.stderr)
        return 1

    finally:
        if driver is not None:
            driver.arreter()


if __name__ == '__main__':
    raise SystemExit(main())
