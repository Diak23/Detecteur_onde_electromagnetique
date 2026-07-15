#!/usr/bin/env python3
"""
interface_ble_v3.py

Interface graphique pour l'analyse des trames BLE capturées avec
l'Adafruit Bluefruit LE Sniffer nRF51822.

Dépendance locale :
    ble_frame_driver_corrige.py

Fonctions :
- démarrage/arrêt de la capture ;
- affichage de la dernière trame ;
- courbe RSSI en temps réel ;
- courbe durée des trames en temps réel ;
- statistiques min/max/moyenne ;
- compteur de trames ;
- sauvegarde PNG ;
- génération d'un rapport TXT ;
- CSV automatique géré par le driver.
"""

from __future__ import annotations

import queue
import statistics
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from ble_frame_driver_corrige import BLEFrame, BLESnifferDriver


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

DOSSIER_SORTIE = Path("acquisitions_ble_interface")
FICHIER_CSV = DOSSIER_SORTIE / "trames_ble_interface.csv"
FICHIER_PNG = DOSSIER_SORTIE / "graphes_ble.png"
FICHIER_RAPPORT = DOSSIER_SORTIE / "rapport_ble.txt"

NOMBRE_POINTS_AFFICHES = 300
PERIODE_MAJ_MS = 100


# ---------------------------------------------------------------------------
# APPLICATION
# ---------------------------------------------------------------------------

class InterfaceBLE:
    def __init__(self, fenetre: tk.Tk) -> None:
        self.fenetre = fenetre
        self.fenetre.title("Analyseur BLE nRF51822 - Durée des trames")
        self.fenetre.geometry("1450x850")
        self.fenetre.minsize(1100, 700)

        self.driver: Optional[BLESnifferDriver] = None
        self.file_trames: queue.Queue[BLEFrame] = queue.Queue()
        self.capture_active = False
        self.heure_debut: Optional[float] = None

        self.trames: list[BLEFrame] = []
        self.temps_relatifs: list[float] = []
        self.rssi: list[int] = []
        self.durees: list[float] = []

        DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

        self._configurer_style()
        self._construire_interface()
        self._configurer_graphes()

        self.fenetre.protocol("WM_DELETE_WINDOW", self.fermer)
        self.fenetre.after(PERIODE_MAJ_MS, self._traiter_file)

    # ------------------------------------------------------------------
    # INTERFACE
    # ------------------------------------------------------------------

    def _configurer_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Titre.TLabel", font=("Arial", 19, "bold"))
        style.configure("Valeur.TLabel", font=("Arial", 11, "bold"))
        style.configure("Etat.TLabel", font=("Arial", 11))

    def _construire_interface(self) -> None:
        conteneur = ttk.Frame(self.fenetre, padding=10)
        conteneur.pack(fill="both", expand=True)

        # Panneau gauche
        panneau = ttk.Frame(conteneur, width=300)
        panneau.pack(side="left", fill="y", padx=(0, 10))
        panneau.pack_propagate(False)

        ttk.Label(
            panneau,
            text="Analyseur BLE V3",
            style="Titre.TLabel",
        ).pack(pady=(0, 10))

        cadre_commandes = ttk.LabelFrame(
            panneau,
            text="Acquisition",
            padding=10,
        )
        cadre_commandes.pack(fill="x", pady=5)

        self.bouton_demarrer = ttk.Button(
            cadre_commandes,
            text="Démarrer",
            command=self.demarrer,
        )
        self.bouton_demarrer.pack(fill="x", pady=3)

        self.bouton_arreter = ttk.Button(
            cadre_commandes,
            text="Arrêter",
            command=self.arreter,
            state="disabled",
        )
        self.bouton_arreter.pack(fill="x", pady=3)

        ttk.Button(
            cadre_commandes,
            text="Réinitialiser l'affichage",
            command=self.reinitialiser,
        ).pack(fill="x", pady=3)

        cadre_sauvegarde = ttk.LabelFrame(
            panneau,
            text="Sauvegardes",
            padding=10,
        )
        cadre_sauvegarde.pack(fill="x", pady=5)

        ttk.Button(
            cadre_sauvegarde,
            text="Sauvegarder les graphes PNG",
            command=self.sauvegarder_png,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_sauvegarde,
            text="Générer le rapport TXT",
            command=self.generer_rapport,
        ).pack(fill="x", pady=3)

        cadre_etat = ttk.LabelFrame(
            panneau,
            text="État",
            padding=10,
        )
        cadre_etat.pack(fill="x", pady=5)

        self.label_etat = ttk.Label(
            cadre_etat,
            text="En attente",
            style="Etat.TLabel",
            wraplength=250,
        )
        self.label_etat.pack(anchor="w")

        self.label_interface = ttk.Label(
            cadre_etat,
            text="Interface : détection automatique",
            wraplength=250,
        )
        self.label_interface.pack(anchor="w", pady=(5, 0))

        self.label_csv = ttk.Label(
            cadre_etat,
            text=f"CSV : {FICHIER_CSV}",
            wraplength=250,
        )
        self.label_csv.pack(anchor="w", pady=(5, 0))

        # Zone droite
        droite = ttk.Frame(conteneur)
        droite.pack(side="left", fill="both", expand=True)

        cadre_trame = ttk.LabelFrame(
            droite,
            text="Dernière trame BLE",
            padding=10,
        )
        cadre_trame.pack(fill="x", pady=(0, 8))

        self.vars_trame = {
            "numero": tk.StringVar(value="Trame : ---"),
            "mac": tk.StringVar(value="MAC : ---"),
            "rssi": tk.StringVar(value="RSSI : ---"),
            "canal": tk.StringVar(value="Canal : ---"),
            "longueur": tk.StringVar(value="Payload : ---"),
            "duree": tk.StringVar(value="Durée : ---"),
            "debut": tk.StringVar(value="Début : ---"),
            "fin": tk.StringVar(value="Fin : ---"),
            "type": tk.StringVar(value="Type PDU : ---"),
        }

        positions = [
            ("numero", 0, 0),
            ("mac", 0, 1),
            ("rssi", 1, 0),
            ("canal", 1, 1),
            ("longueur", 2, 0),
            ("duree", 2, 1),
            ("debut", 3, 0),
            ("fin", 3, 1),
            ("type", 4, 0),
        ]

        for nom, ligne, colonne in positions:
            ttk.Label(
                cadre_trame,
                textvariable=self.vars_trame[nom],
                style="Valeur.TLabel",
            ).grid(
                row=ligne,
                column=colonne,
                sticky="w",
                padx=12,
                pady=3,
            )

        cadre_trame.columnconfigure(0, weight=1)
        cadre_trame.columnconfigure(1, weight=1)

        cadre_stats = ttk.LabelFrame(
            droite,
            text="Statistiques",
            padding=10,
        )
        cadre_stats.pack(fill="x", pady=(0, 8))

        self.vars_stats = {
            "nb": tk.StringVar(value="Nombre de trames : 0"),
            "duree_moy": tk.StringVar(value="Durée moyenne : ---"),
            "duree_min": tk.StringVar(value="Durée min : ---"),
            "duree_max": tk.StringVar(value="Durée max : ---"),
            "rssi_moy": tk.StringVar(value="RSSI moyen : ---"),
            "rssi_min": tk.StringVar(value="RSSI min : ---"),
            "rssi_max": tk.StringVar(value="RSSI max : ---"),
            "temps": tk.StringVar(value="Temps acquisition : 0,0 s"),
        }

        for index, variable in enumerate(self.vars_stats.values()):
            ligne = index // 4
            colonne = index % 4
            ttk.Label(
                cadre_stats,
                textvariable=variable,
            ).grid(
                row=ligne,
                column=colonne,
                sticky="w",
                padx=12,
                pady=3,
            )

        for colonne in range(4):
            cadre_stats.columnconfigure(colonne, weight=1)

        cadre_graphes = ttk.LabelFrame(
            droite,
            text="Graphes temps réel",
            padding=8,
        )
        cadre_graphes.pack(fill="both", expand=True)

        self.figure, (self.ax_rssi, self.ax_duree) = plt.subplots(
            2,
            1,
            figsize=(10, 7),
        )
        self.figure.subplots_adjust(hspace=0.48)

        self.canvas = FigureCanvasTkAgg(
            self.figure,
            master=cadre_graphes,
        )
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # DRIVER ET ACQUISITION
    # ------------------------------------------------------------------

    def _callback_driver(self, trame: BLEFrame) -> None:
        self.file_trames.put(trame)

    def demarrer(self) -> None:
        if self.capture_active:
            return

        try:
            self.driver = BLESnifferDriver(
                interface=None,
                fichier_csv=FICHIER_CSV,
                callback=self._callback_driver,
            )
            self.driver.demarrer()

            self.capture_active = True
            self.heure_debut = time.time()

            self.label_etat.config(text="Capture BLE en cours")
            self.label_interface.config(
                text=f"Interface : {self.driver.interface}"
            )

            self.bouton_demarrer.config(state="disabled")
            self.bouton_arreter.config(state="normal")

        except Exception as erreur:
            messagebox.showerror(
                "Erreur de démarrage",
                str(erreur),
            )
            self.label_etat.config(text=f"Erreur : {erreur}")

    def arreter(self) -> None:
        if self.driver is not None:
            self.driver.arreter()

        self.capture_active = False
        self.driver = None

        self.label_etat.config(text="Capture arrêtée")
        self.bouton_demarrer.config(state="normal")
        self.bouton_arreter.config(state="disabled")

    def fermer(self) -> None:
        self.arreter()
        self.fenetre.destroy()

    def _traiter_file(self) -> None:
        nouvelles_trames = 0

        while True:
            try:
                trame = self.file_trames.get_nowait()
            except queue.Empty:
                break

            self._ajouter_trame(trame)
            nouvelles_trames += 1

        if nouvelles_trames:
            self._mettre_a_jour_graphes()
            self._mettre_a_jour_stats()

        if self.capture_active and self.heure_debut is not None:
            duree = time.time() - self.heure_debut
            self.vars_stats["temps"].set(
                f"Temps acquisition : {duree:.1f} s"
            )

        self.fenetre.after(PERIODE_MAJ_MS, self._traiter_file)

    def _ajouter_trame(self, trame: BLEFrame) -> None:
        if not self.trames:
            origine = trame.debut_s
        else:
            origine = self.trames[0].debut_s

        self.trames.append(trame)
        self.temps_relatifs.append(trame.debut_s - origine)
        self.durees.append(trame.duree_us)

        if trame.rssi_dbm is not None:
            self.rssi.append(trame.rssi_dbm)
        else:
            self.rssi.append(float("nan"))

        self.vars_trame["numero"].set(f"Trame : {trame.numero}")
        self.vars_trame["mac"].set(f"MAC : {trame.mac}")
        self.vars_trame["rssi"].set(
            f"RSSI : {trame.rssi_dbm if trame.rssi_dbm is not None else '---'} dBm"
        )
        self.vars_trame["canal"].set(
            f"Canal : {trame.canal if trame.canal is not None else '---'}"
        )
        self.vars_trame["longueur"].set(
            f"Payload : {trame.longueur_payload_octets} octets"
        )
        self.vars_trame["duree"].set(
            f"Durée : {trame.duree_us:.1f} µs"
        )
        self.vars_trame["debut"].set(
            f"Début : {trame.debut_s:.9f} s"
        )
        self.vars_trame["fin"].set(
            f"Fin : {trame.fin_s:.9f} s"
        )
        self.vars_trame["type"].set(
            f"Type PDU : {trame.type_pdu}"
        )

    # ------------------------------------------------------------------
    # GRAPHES ET STATISTIQUES
    # ------------------------------------------------------------------

    def _configurer_graphes(self) -> None:
        self.ax_rssi.set_title("RSSI des trames BLE")
        self.ax_rssi.set_xlabel("Temps relatif (s)")
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)

        self.ax_duree.set_title("Durée radio des trames BLE")
        self.ax_duree.set_xlabel("Temps relatif (s)")
        self.ax_duree.set_ylabel("Durée (µs)")
        self.ax_duree.grid(True)

        self.canvas.draw()

    def _mettre_a_jour_graphes(self) -> None:
        debut = max(0, len(self.trames) - NOMBRE_POINTS_AFFICHES)

        temps = self.temps_relatifs[debut:]
        rssi = self.rssi[debut:]
        durees = self.durees[debut:]

        self.ax_rssi.clear()
        self.ax_duree.clear()

        self.ax_rssi.plot(temps, rssi)
        self.ax_duree.plot(temps, durees)

        self._configurer_graphes()

    def _mettre_a_jour_stats(self) -> None:
        if not self.trames:
            return

        rssis_valides = [
            valeur for valeur in self.rssi
            if valeur == valeur
        ]

        self.vars_stats["nb"].set(
            f"Nombre de trames : {len(self.trames)}"
        )
        self.vars_stats["duree_moy"].set(
            f"Durée moyenne : {statistics.mean(self.durees):.1f} µs"
        )
        self.vars_stats["duree_min"].set(
            f"Durée min : {min(self.durees):.1f} µs"
        )
        self.vars_stats["duree_max"].set(
            f"Durée max : {max(self.durees):.1f} µs"
        )

        if rssis_valides:
            self.vars_stats["rssi_moy"].set(
                f"RSSI moyen : {statistics.mean(rssis_valides):.1f} dBm"
            )
            self.vars_stats["rssi_min"].set(
                f"RSSI min : {min(rssis_valides)} dBm"
            )
            self.vars_stats["rssi_max"].set(
                f"RSSI max : {max(rssis_valides)} dBm"
            )

    # ------------------------------------------------------------------
    # SAUVEGARDE
    # ------------------------------------------------------------------

    def sauvegarder_png(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune trame n'a encore été capturée.",
            )
            return

        self.figure.savefig(FICHIER_PNG, dpi=300)
        self.label_etat.config(
            text=f"Graphes sauvegardés : {FICHIER_PNG}"
        )

    def generer_rapport(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune trame n'a encore été capturée.",
            )
            return

        rssis_valides = [
            valeur for valeur in self.rssi
            if valeur == valeur
        ]

        with FICHIER_RAPPORT.open("w", encoding="utf-8") as rapport:
            rapport.write("RAPPORT D'ACQUISITION BLE\n")
            rapport.write("=========================\n\n")
            rapport.write(f"Date : {datetime.now()}\n")
            rapport.write("Matériel : Adafruit Bluefruit LE Sniffer nRF51822\n")
            rapport.write("PHY : LE 1M\n")
            rapport.write(f"Nombre de trames : {len(self.trames)}\n\n")

            rapport.write("DURÉE DES TRAMES\n")
            rapport.write("-----------------\n")
            rapport.write(
                f"Durée moyenne : {statistics.mean(self.durees):.3f} µs\n"
            )
            rapport.write(
                f"Durée minimale : {min(self.durees):.3f} µs\n"
            )
            rapport.write(
                f"Durée maximale : {max(self.durees):.3f} µs\n\n"
            )

            if rssis_valides:
                rapport.write("RSSI\n")
                rapport.write("----\n")
                rapport.write(
                    f"RSSI moyen : {statistics.mean(rssis_valides):.2f} dBm\n"
                )
                rapport.write(
                    f"RSSI minimal : {min(rssis_valides)} dBm\n"
                )
                rapport.write(
                    f"RSSI maximal : {max(rssis_valides)} dBm\n\n"
                )

            rapport.write("RÉPARTITION PAR CANAL\n")
            rapport.write("---------------------\n")

            for canal in (37, 38, 39):
                nombre = sum(
                    1 for trame in self.trames
                    if trame.canal == canal
                )
                rapport.write(f"Canal {canal} : {nombre} trames\n")

        self.label_etat.config(
            text=f"Rapport généré : {FICHIER_RAPPORT}"
        )

    def reinitialiser(self) -> None:
        self.trames.clear()
        self.temps_relatifs.clear()
        self.rssi.clear()
        self.durees.clear()

        for nom, variable in self.vars_trame.items():
            libelle = {
                "numero": "Trame",
                "mac": "MAC",
                "rssi": "RSSI",
                "canal": "Canal",
                "longueur": "Payload",
                "duree": "Durée",
                "debut": "Début",
                "fin": "Fin",
                "type": "Type PDU",
            }[nom]
            variable.set(f"{libelle} : ---")

        self.vars_stats["nb"].set("Nombre de trames : 0")
        self.vars_stats["duree_moy"].set("Durée moyenne : ---")
        self.vars_stats["duree_min"].set("Durée min : ---")
        self.vars_stats["duree_max"].set("Durée max : ---")
        self.vars_stats["rssi_moy"].set("RSSI moyen : ---")
        self.vars_stats["rssi_min"].set("RSSI min : ---")
        self.vars_stats["rssi_max"].set("RSSI max : ---")
        self.vars_stats["temps"].set("Temps acquisition : 0,0 s")

        self.ax_rssi.clear()
        self.ax_duree.clear()
        self._configurer_graphes()

        self.label_etat.config(text="Affichage réinitialisé")


def main() -> None:
    fenetre = tk.Tk()
    InterfaceBLE(fenetre)
    fenetre.mainloop()


if __name__ == "__main__":
    main()
