#!/usr/bin/env python3
"""
interface_ble_v6_selection_appareil.py

Interface BLE optimisée pour Raspberry Pi + nRF51822.

Fonctions principales
---------------------
1. Recherche des appareils BLE avant l'acquisition avec bluetoothctl.
2. Sélection obligatoire d'un appareil avant d'activer le bouton Démarrer.
3. Filtrage de l'acquisition sur une seule adresse MAC.
4. Onglet graphique 1 :
      - RSSI en fonction du temps ;
      - durée des trames en fonction du temps.
5. Onglet graphique 2 :
      - histogramme des durées ;
      - répartition des canaux 37, 38 et 39.
6. Suppression de la chronologie.
7. Tableau des trames, statistiques, CSV, PNG et rapport TXT.
8. Mises à jour limitées pour garder l'interface fluide.

Dépendance locale obligatoire
-----------------------------
Le fichier suivant doit être placé dans le même dossier :
    ble_frame_driver_corrige.py
"""

from __future__ import annotations

import csv
import queue
import re
import statistics
import subprocess
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from ble_frame_driver_corrige import BLEFrame, BLESnifferDriver


# =============================================================================
# CONFIGURATION
# =============================================================================

DOSSIER_SORTIE = Path("acquisitions_ble_v6")
CSV_BRUT = DOSSIER_SORTIE / "trames_ble_brutes.csv"
CSV_FILTRE = DOSSIER_SORTIE / "trames_ble_appareil_selectionne.csv"
CSV_STATS = DOSSIER_SORTIE / "statistiques_ble.csv"
PNG_TEMPOREL = DOSSIER_SORTIE / "rssi_et_duree.png"
PNG_DISTRIBUTION = DOSSIER_SORTIE / "histogramme_et_canaux.png"
RAPPORT_TXT = DOSSIER_SORTIE / "rapport_ble.txt"

PERIODE_LECTURE_MS = 100
PERIODE_TABLE_MS = 500
PERIODE_GRAPHES_MS = 1000

MAX_TRAMES_PAR_CYCLE = 200
MAX_LIGNES_TABLEAU = 300
MAX_POINTS_GRAPHE = 1800

DUREE_SCAN_BLUETOOTH_S = 8
MAC_REGEX = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


# =============================================================================
# APPLICATION
# =============================================================================

class InterfaceBLESelection:
    def __init__(self, fenetre: tk.Tk) -> None:
        self.fenetre = fenetre
        self.fenetre.title(
            "Analyseur BLE — sélection d'un appareil avant acquisition"
        )
        self.fenetre.geometry("1580x920")
        self.fenetre.minsize(1200, 760)

        DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

        # Driver et acquisition
        self.driver: Optional[BLESnifferDriver] = None
        self.capture_active = False
        self.file_trames: queue.Queue[BLEFrame] = queue.Queue()
        self.temps_demarrage_local: Optional[float] = None

        # Appareil choisi
        self.appareils_detectes: dict[str, str] = {}
        self.mac_selectionnee: Optional[str] = None
        self.nom_selectionne = "Inconnu"

        # Données retenues uniquement pour l'appareil choisi
        self.trames: list[BLEFrame] = []

        # Gestion des rafraîchissements
        self.derniere_maj_table = 0.0
        self.derniere_maj_graphes = 0.0
        self.interface_sale = False
        self.scan_en_cours = False

        self._configurer_style()
        self._construire_interface()
        self._configurer_graphes_vides()

        self.fenetre.protocol("WM_DELETE_WINDOW", self.fermer)
        self.fenetre.after(PERIODE_LECTURE_MS, self._traiter_file)

        # Chargement initial des appareils déjà connus/appairés
        self._charger_appareils_connus()

    # =========================================================================
    # STYLE
    # =========================================================================

    def _configurer_style(self) -> None:
        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Titre.TLabel", font=("Arial", 18, "bold"))
        style.configure("Valeur.TLabel", font=("Arial", 10, "bold"))
        style.configure("Etat.TLabel", font=("Arial", 10))
        style.configure("Treeview", rowheight=24)

    # =========================================================================
    # CONSTRUCTION DE L'INTERFACE
    # =========================================================================

    def _construire_interface(self) -> None:
        principal = ttk.Frame(self.fenetre, padding=10)
        principal.pack(fill="both", expand=True)

        self._construire_panneau_gauche(principal)
        self._construire_zone_droite(principal)

    def _construire_panneau_gauche(self, parent: ttk.Frame) -> None:
        panneau = ttk.Frame(parent, width=340)
        panneau.pack(side="left", fill="y", padx=(0, 10))
        panneau.pack_propagate(False)

        ttk.Label(
            panneau,
            text="Analyseur BLE",
            style="Titre.TLabel",
        ).pack(pady=(0, 10))

        # ---------------------------------------------------------------------
        # Sélection préalable
        # ---------------------------------------------------------------------
        cadre_selection = ttk.LabelFrame(
            panneau,
            text="1. Choisir l'appareil BLE",
            padding=10,
        )
        cadre_selection.pack(fill="x", pady=5)

        self.var_appareil = tk.StringVar(value="Aucun appareil sélectionné")

        self.combo_appareils = ttk.Combobox(
            cadre_selection,
            textvariable=self.var_appareil,
            values=[],
            state="readonly",
        )
        self.combo_appareils.pack(fill="x", pady=3)
        self.combo_appareils.bind(
            "<<ComboboxSelected>>",
            self._selectionner_appareil,
        )

        self.btn_scanner = ttk.Button(
            cadre_selection,
            text=f"Rechercher les appareils ({DUREE_SCAN_BLUETOOTH_S} s)",
            command=self.lancer_scan,
        )
        self.btn_scanner.pack(fill="x", pady=3)

        ttk.Label(
            cadre_selection,
            text=(
                "La recherche utilise bluetoothctl. "
                "Le bouton Démarrer reste bloqué tant qu'aucun "
                "appareil n'est sélectionné."
            ),
            wraplength=290,
        ).pack(anchor="w", pady=(5, 0))

        self.label_cible = ttk.Label(
            cadre_selection,
            text="Cible : aucune",
            wraplength=290,
        )
        self.label_cible.pack(anchor="w", pady=(5, 0))

        # ---------------------------------------------------------------------
        # Acquisition
        # ---------------------------------------------------------------------
        cadre_acquisition = ttk.LabelFrame(
            panneau,
            text="2. Acquisition",
            padding=10,
        )
        cadre_acquisition.pack(fill="x", pady=5)

        self.btn_demarrer = ttk.Button(
            cadre_acquisition,
            text="Démarrer l'acquisition",
            command=self.demarrer,
            state="disabled",
        )
        self.btn_demarrer.pack(fill="x", pady=3)

        self.btn_arreter = ttk.Button(
            cadre_acquisition,
            text="Arrêter",
            command=self.arreter,
            state="disabled",
        )
        self.btn_arreter.pack(fill="x", pady=3)

        ttk.Button(
            cadre_acquisition,
            text="Réinitialiser les mesures",
            command=self.reinitialiser,
        ).pack(fill="x", pady=3)

        # ---------------------------------------------------------------------
        # Export
        # ---------------------------------------------------------------------
        cadre_export = ttk.LabelFrame(
            panneau,
            text="3. Export",
            padding=10,
        )
        cadre_export.pack(fill="x", pady=5)

        ttk.Button(
            cadre_export,
            text="Exporter CSV",
            command=self.exporter_csv,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Exporter statistiques",
            command=self.exporter_statistiques,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Sauvegarder fenêtre graphique 1",
            command=self.sauvegarder_graphe_temporel,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Sauvegarder fenêtre graphique 2",
            command=self.sauvegarder_graphe_distribution,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Générer le rapport TXT",
            command=self.generer_rapport,
        ).pack(fill="x", pady=3)

        # ---------------------------------------------------------------------
        # État
        # ---------------------------------------------------------------------
        cadre_etat = ttk.LabelFrame(
            panneau,
            text="État",
            padding=10,
        )
        cadre_etat.pack(fill="x", pady=5)

        self.label_etat = ttk.Label(
            cadre_etat,
            text="Sélectionne un appareil BLE.",
            style="Etat.TLabel",
            wraplength=290,
        )
        self.label_etat.pack(anchor="w")

        self.label_interface = ttk.Label(
            cadre_etat,
            text="Sniffer : détection automatique",
            wraplength=290,
        )
        self.label_interface.pack(anchor="w", pady=(4, 0))

        self.label_fichier = ttk.Label(
            cadre_etat,
            text=f"CSV brut : {CSV_BRUT}",
            wraplength=290,
        )
        self.label_fichier.pack(anchor="w", pady=(4, 0))

    def _construire_zone_droite(self, parent: ttk.Frame) -> None:
        zone = ttk.Frame(parent)
        zone.pack(side="left", fill="both", expand=True)

        self.notebook = ttk.Notebook(zone)
        self.notebook.pack(fill="both", expand=True)

        self.onglet_mesures = ttk.Frame(self.notebook)
        self.onglet_trames = ttk.Frame(self.notebook)
        self.onglet_graphe_1 = ttk.Frame(self.notebook)
        self.onglet_graphe_2 = ttk.Frame(self.notebook)

        self.notebook.add(self.onglet_mesures, text="Mesures")
        self.notebook.add(self.onglet_trames, text="Trames")
        self.notebook.add(
            self.onglet_graphe_1,
            text="Graphiques 1 — RSSI et durée",
        )
        self.notebook.add(
            self.onglet_graphe_2,
            text="Graphiques 2 — histogramme et canaux",
        )

        self.notebook.bind(
            "<<NotebookTabChanged>>",
            self._changement_onglet,
        )

        self._construire_onglet_mesures()
        self._construire_onglet_trames()
        self._construire_onglet_graphe_1()
        self._construire_onglet_graphe_2()

    def _construire_onglet_mesures(self) -> None:
        cadre_derniere = ttk.LabelFrame(
            self.onglet_mesures,
            text="Dernière trame de l'appareil sélectionné",
            padding=10,
        )
        cadre_derniere.pack(fill="x", padx=8, pady=8)

        self.vars_trame = {
            "numero": tk.StringVar(value="Trame : ---"),
            "appareil": tk.StringVar(value="Appareil : ---"),
            "mac": tk.StringVar(value="MAC : ---"),
            "rssi": tk.StringVar(value="RSSI : ---"),
            "canal": tk.StringVar(value="Canal : ---"),
            "payload": tk.StringVar(value="Payload : ---"),
            "duree": tk.StringVar(value="Durée : ---"),
            "type": tk.StringVar(value="Type PDU : ---"),
            "debut": tk.StringVar(value="Début : ---"),
            "fin": tk.StringVar(value="Fin : ---"),
        }

        positions = [
            ("numero", 0, 0),
            ("appareil", 0, 1),
            ("mac", 1, 0),
            ("rssi", 1, 1),
            ("canal", 2, 0),
            ("payload", 2, 1),
            ("duree", 3, 0),
            ("type", 3, 1),
            ("debut", 4, 0),
            ("fin", 4, 1),
        ]

        for cle, ligne, colonne in positions:
            ttk.Label(
                cadre_derniere,
                textvariable=self.vars_trame[cle],
                style="Valeur.TLabel",
            ).grid(
                row=ligne,
                column=colonne,
                sticky="w",
                padx=12,
                pady=4,
            )

        cadre_derniere.columnconfigure(0, weight=1)
        cadre_derniere.columnconfigure(1, weight=1)

        cadre_stats = ttk.LabelFrame(
            self.onglet_mesures,
            text="Statistiques de l'appareil sélectionné",
            padding=10,
        )
        cadre_stats.pack(fill="x", padx=8, pady=8)

        self.vars_stats = {
            "nombre": tk.StringVar(value="Nombre de trames : 0"),
            "temps": tk.StringVar(value="Temps acquisition : 0,0 s"),
            "duree_moy": tk.StringVar(value="Durée moyenne : ---"),
            "duree_min": tk.StringVar(value="Durée min : ---"),
            "duree_max": tk.StringVar(value="Durée max : ---"),
            "rssi_moy": tk.StringVar(value="RSSI moyen : ---"),
            "rssi_min": tk.StringVar(value="RSSI min : ---"),
            "rssi_max": tk.StringVar(value="RSSI max : ---"),
            "payload_moy": tk.StringVar(value="Payload moyen : ---"),
            "debit": tk.StringVar(value="Débit : --- trames/s"),
            "intervalle": tk.StringVar(value="Intervalle moyen : ---"),
            "canaux": tk.StringVar(value="Canaux : 37=0, 38=0, 39=0"),
        }

        for index, variable in enumerate(self.vars_stats.values()):
            ligne = index // 3
            colonne = index % 3
            ttk.Label(
                cadre_stats,
                textvariable=variable,
            ).grid(
                row=ligne,
                column=colonne,
                sticky="w",
                padx=12,
                pady=4,
            )

        for colonne in range(3):
            cadre_stats.columnconfigure(colonne, weight=1)

    def _construire_onglet_trames(self) -> None:
        cadre = ttk.Frame(self.onglet_trames, padding=8)
        cadre.pack(fill="both", expand=True)

        colonnes = (
            "numero",
            "debut",
            "fin",
            "duree",
            "mac",
            "rssi",
            "canal",
            "payload",
            "type",
        )

        self.table_trames = ttk.Treeview(
            cadre,
            columns=colonnes,
            show="headings",
        )

        titres = {
            "numero": "#",
            "debut": "Début (s)",
            "fin": "Fin (s)",
            "duree": "Durée (µs)",
            "mac": "MAC",
            "rssi": "RSSI (dBm)",
            "canal": "Canal",
            "payload": "Payload",
            "type": "Type PDU",
        }

        largeurs = {
            "numero": 60,
            "debut": 160,
            "fin": 160,
            "duree": 100,
            "mac": 150,
            "rssi": 90,
            "canal": 70,
            "payload": 90,
            "type": 100,
        }

        for colonne in colonnes:
            self.table_trames.heading(
                colonne,
                text=titres[colonne],
            )
            self.table_trames.column(
                colonne,
                width=largeurs[colonne],
                anchor="center",
            )

        barre_y = ttk.Scrollbar(
            cadre,
            orient="vertical",
            command=self.table_trames.yview,
        )
        barre_x = ttk.Scrollbar(
            cadre,
            orient="horizontal",
            command=self.table_trames.xview,
        )

        self.table_trames.configure(
            yscrollcommand=barre_y.set,
            xscrollcommand=barre_x.set,
        )

        self.table_trames.grid(row=0, column=0, sticky="nsew")
        barre_y.grid(row=0, column=1, sticky="ns")
        barre_x.grid(row=1, column=0, sticky="ew")

        cadre.rowconfigure(0, weight=1)
        cadre.columnconfigure(0, weight=1)

    def _construire_onglet_graphe_1(self) -> None:
        cadre = ttk.Frame(self.onglet_graphe_1, padding=8)
        cadre.pack(fill="both", expand=True)

        self.fig_temporel, (
            self.ax_rssi,
            self.ax_duree,
        ) = plt.subplots(2, 1, figsize=(11, 8))

        self.fig_temporel.subplots_adjust(hspace=0.48)

        self.canvas_temporel = FigureCanvasTkAgg(
            self.fig_temporel,
            master=cadre,
        )
        self.canvas_temporel.get_tk_widget().pack(
            fill="both",
            expand=True,
        )

    def _construire_onglet_graphe_2(self) -> None:
        cadre = ttk.Frame(self.onglet_graphe_2, padding=8)
        cadre.pack(fill="both", expand=True)

        self.fig_distribution, (
            self.ax_histogramme,
            self.ax_canaux,
        ) = plt.subplots(1, 2, figsize=(12, 7))

        self.fig_distribution.subplots_adjust(wspace=0.30)

        self.canvas_distribution = FigureCanvasTkAgg(
            self.fig_distribution,
            master=cadre,
        )
        self.canvas_distribution.get_tk_widget().pack(
            fill="both",
            expand=True,
        )

    # =========================================================================
    # RECHERCHE ET SÉLECTION DE L'APPAREIL
    # =========================================================================

    def _charger_appareils_connus(self) -> None:
        """
        Charge les appareils déjà mémorisés/appairés par BlueZ.
        """
        try:
            sortie = subprocess.check_output(
                ["bluetoothctl", "devices"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=4,
            )
            self._extraire_appareils(sortie)
            self._actualiser_liste_appareils()

        except Exception:
            pass

    def lancer_scan(self) -> None:
        if self.scan_en_cours or self.capture_active:
            return

        self.scan_en_cours = True
        self.btn_scanner.config(state="disabled")
        self.btn_demarrer.config(state="disabled")
        self.label_etat.config(
            text=(
                f"Recherche BLE en cours pendant "
                f"{DUREE_SCAN_BLUETOOTH_S} secondes…"
            )
        )

        threading.Thread(
            target=self._scan_bluetooth_thread,
            daemon=True,
        ).start()

    def _scan_bluetooth_thread(self) -> None:
        try:
            subprocess.run(
                ["bluetoothctl", "power", "on"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            processus = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            assert processus.stdin is not None
            processus.stdin.write("scan on\n")
            processus.stdin.flush()

            time.sleep(DUREE_SCAN_BLUETOOTH_S)

            processus.stdin.write("scan off\n")
            processus.stdin.flush()
            processus.terminate()

            sortie = subprocess.check_output(
                ["bluetoothctl", "devices"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )

            self.fenetre.after(
                0,
                lambda: self._terminer_scan(sortie, None),
            )

        except Exception as erreur:
            self.fenetre.after(
                0,
                lambda: self._terminer_scan("", erreur),
            )

    def _terminer_scan(
        self,
        sortie: str,
        erreur: Optional[Exception],
    ) -> None:
        self.scan_en_cours = False
        self.btn_scanner.config(state="normal")

        if erreur is not None:
            self.label_etat.config(
                text=f"Erreur pendant le scan : {erreur}"
            )
            return

        self._extraire_appareils(sortie)
        self._actualiser_liste_appareils()

        self.label_etat.config(
            text=(
                f"Recherche terminée : "
                f"{len(self.appareils_detectes)} appareil(s). "
                "Sélectionne la cible."
            )
        )

    def _extraire_appareils(self, sortie: str) -> None:
        for ligne in sortie.splitlines():
            morceaux = ligne.strip().split(maxsplit=2)

            if len(morceaux) < 2 or morceaux[0] != "Device":
                continue

            mac = morceaux[1].upper()

            if not MAC_REGEX.fullmatch(mac):
                continue

            nom = morceaux[2] if len(morceaux) >= 3 else "Inconnu"
            self.appareils_detectes[mac] = nom

    def _actualiser_liste_appareils(self) -> None:
        valeurs = [
            f"{nom} | {mac}"
            for mac, nom in sorted(
                self.appareils_detectes.items(),
                key=lambda element: element[1].lower(),
            )
        ]

        self.combo_appareils.configure(values=valeurs)

    def _selectionner_appareil(self, _event: object = None) -> None:
        selection = self.var_appareil.get()
        morceaux = selection.rsplit("|", maxsplit=1)

        if len(morceaux) != 2:
            self.mac_selectionnee = None
            self.btn_demarrer.config(state="disabled")
            return

        nom = morceaux[0].strip()
        mac = morceaux[1].strip().upper()

        if not MAC_REGEX.fullmatch(mac):
            self.mac_selectionnee = None
            self.btn_demarrer.config(state="disabled")
            return

        self.nom_selectionne = nom
        self.mac_selectionnee = mac
        self.label_cible.config(
            text=f"Cible : {nom}\nMAC : {mac}"
        )
        self.label_etat.config(
            text="Appareil sélectionné. L'acquisition peut démarrer."
        )

        if not self.capture_active:
            self.btn_demarrer.config(state="normal")

    # =========================================================================
    # ACQUISITION
    # =========================================================================

    def _callback_driver(self, trame: BLEFrame) -> None:
        self.file_trames.put(trame)

    def demarrer(self) -> None:
        if self.capture_active:
            return

        if self.mac_selectionnee is None:
            messagebox.showwarning(
                "Appareil requis",
                "Sélectionne un appareil avant de démarrer.",
            )
            return

        try:
            # Nouvelle session de mesures pour l'appareil choisi
            self.reinitialiser()

            self.driver = BLESnifferDriver(
                interface=None,
                fichier_csv=CSV_BRUT,
                callback=self._callback_driver,
            )
            self.driver.demarrer()

            self.capture_active = True
            self.temps_demarrage_local = time.time()

            self.combo_appareils.config(state="disabled")
            self.btn_scanner.config(state="disabled")
            self.btn_demarrer.config(state="disabled")
            self.btn_arreter.config(state="normal")

            self.label_interface.config(
                text=f"Sniffer : {self.driver.interface}"
            )
            self.label_etat.config(
                text=(
                    f"Acquisition de {self.nom_selectionne} "
                    f"({self.mac_selectionnee}) en cours."
                )
            )

        except Exception as erreur:
            messagebox.showerror(
                "Erreur de démarrage",
                str(erreur),
            )
            self.label_etat.config(text=f"Erreur : {erreur}")

    def arreter(self) -> None:
        if self.driver is not None:
            self.driver.arreter()

        self.driver = None
        self.capture_active = False

        self.combo_appareils.config(state="readonly")
        self.btn_scanner.config(state="normal")
        self.btn_arreter.config(state="disabled")

        if self.mac_selectionnee is not None:
            self.btn_demarrer.config(state="normal")

        self.label_etat.config(
            text=(
                f"Acquisition arrêtée. "
                f"{len(self.trames)} trame(s) retenue(s)."
            )
        )

        self._rafraichir_table()
        self._rafraichir_statistiques()
        self._rafraichir_graphe_temporel()
        self._rafraichir_graphe_distribution()

    def fermer(self) -> None:
        self.arreter()
        self.fenetre.destroy()

    def _traiter_file(self) -> None:
        nouvelles_retenues = 0
        traitees = 0

        while traitees < MAX_TRAMES_PAR_CYCLE:
            try:
                trame = self.file_trames.get_nowait()
            except queue.Empty:
                break

            traitees += 1

            if (
                self.mac_selectionnee is not None
                and trame.mac.upper() == self.mac_selectionnee.upper()
            ):
                self.trames.append(trame)
                self._afficher_derniere_trame(trame)
                nouvelles_retenues += 1

        if nouvelles_retenues:
            self.interface_sale = True

        maintenant = time.monotonic()

        if self.interface_sale:
            if (
                maintenant - self.derniere_maj_table
                >= PERIODE_TABLE_MS / 1000.0
            ):
                self._rafraichir_table()
                self._rafraichir_statistiques()
                self.derniere_maj_table = maintenant

            onglet = self.notebook.index(
                self.notebook.select()
            )

            if (
                onglet in (2, 3)
                and maintenant - self.derniere_maj_graphes
                >= PERIODE_GRAPHES_MS / 1000.0
            ):
                if onglet == 2:
                    self._rafraichir_graphe_temporel()
                else:
                    self._rafraichir_graphe_distribution()

                self.derniere_maj_graphes = maintenant

            self.interface_sale = False

        if (
            self.capture_active
            and self.temps_demarrage_local is not None
        ):
            duree = time.time() - self.temps_demarrage_local
            self.vars_stats["temps"].set(
                f"Temps acquisition : {duree:.1f} s"
            )

        self.fenetre.after(
            PERIODE_LECTURE_MS,
            self._traiter_file,
        )

    # =========================================================================
    # AFFICHAGE
    # =========================================================================

    def _afficher_derniere_trame(
        self,
        trame: BLEFrame,
    ) -> None:
        self.vars_trame["numero"].set(
            f"Trame : {trame.numero}"
        )
        self.vars_trame["appareil"].set(
            f"Appareil : {self.nom_selectionne}"
        )
        self.vars_trame["mac"].set(
            f"MAC : {trame.mac}"
        )
        self.vars_trame["rssi"].set(
            "RSSI : "
            + (
                "---"
                if trame.rssi_dbm is None
                else f"{trame.rssi_dbm} dBm"
            )
        )
        self.vars_trame["canal"].set(
            "Canal : "
            + (
                "---"
                if trame.canal is None
                else str(trame.canal)
            )
        )
        self.vars_trame["payload"].set(
            f"Payload : "
            f"{trame.longueur_payload_octets} octets"
        )
        self.vars_trame["duree"].set(
            f"Durée : {trame.duree_us:.1f} µs"
        )
        self.vars_trame["type"].set(
            f"Type PDU : {trame.type_pdu}"
        )
        self.vars_trame["debut"].set(
            f"Début : {trame.debut_s:.9f} s"
        )
        self.vars_trame["fin"].set(
            f"Fin : {trame.fin_s:.9f} s"
        )

    def _rafraichir_table(self) -> None:
        for iid in self.table_trames.get_children():
            self.table_trames.delete(iid)

        for trame in self.trames[-MAX_LIGNES_TABLEAU:]:
            self.table_trames.insert(
                "",
                "end",
                values=(
                    trame.numero,
                    f"{trame.debut_s:.9f}",
                    f"{trame.fin_s:.9f}",
                    f"{trame.duree_us:.1f}",
                    trame.mac,
                    (
                        "---"
                        if trame.rssi_dbm is None
                        else trame.rssi_dbm
                    ),
                    (
                        "---"
                        if trame.canal is None
                        else trame.canal
                    ),
                    trame.longueur_payload_octets,
                    trame.type_pdu,
                ),
            )

    def _rafraichir_statistiques(self) -> None:
        self.vars_stats["nombre"].set(
            f"Nombre de trames : {len(self.trames)}"
        )

        if not self.trames:
            return

        durees = [
            trame.duree_us
            for trame in self.trames
        ]
        rssis = [
            trame.rssi_dbm
            for trame in self.trames
            if trame.rssi_dbm is not None
        ]
        payloads = [
            trame.longueur_payload_octets
            for trame in self.trames
        ]

        self.vars_stats["duree_moy"].set(
            f"Durée moyenne : "
            f"{statistics.mean(durees):.1f} µs"
        )
        self.vars_stats["duree_min"].set(
            f"Durée min : {min(durees):.1f} µs"
        )
        self.vars_stats["duree_max"].set(
            f"Durée max : {max(durees):.1f} µs"
        )
        self.vars_stats["payload_moy"].set(
            f"Payload moyen : "
            f"{statistics.mean(payloads):.1f} octets"
        )

        if rssis:
            self.vars_stats["rssi_moy"].set(
                f"RSSI moyen : "
                f"{statistics.mean(rssis):.1f} dBm"
            )
            self.vars_stats["rssi_min"].set(
                f"RSSI min : {min(rssis)} dBm"
            )
            self.vars_stats["rssi_max"].set(
                f"RSSI max : {max(rssis)} dBm"
            )

        compte_canaux = Counter(
            trame.canal
            for trame in self.trames
            if trame.canal in (37, 38, 39)
        )
        self.vars_stats["canaux"].set(
            "Canaux : "
            f"37={compte_canaux.get(37, 0)}, "
            f"38={compte_canaux.get(38, 0)}, "
            f"39={compte_canaux.get(39, 0)}"
        )

        if len(self.trames) >= 2:
            duree_totale = (
                self.trames[-1].debut_s
                - self.trames[0].debut_s
            )

            if duree_totale > 0:
                debit = (
                    len(self.trames) - 1
                ) / duree_totale
                self.vars_stats["debit"].set(
                    f"Débit : {debit:.2f} trames/s"
                )

            intervalles = [
                self.trames[index].debut_s
                - self.trames[index - 1].debut_s
                for index in range(1, len(self.trames))
            ]
            self.vars_stats["intervalle"].set(
                f"Intervalle moyen : "
                f"{statistics.mean(intervalles):.4f} s"
            )

    # =========================================================================
    # GRAPHES
    # =========================================================================

    def _configurer_graphes_vides(self) -> None:
        self.ax_rssi.set_title(
            "RSSI de l'appareil sélectionné"
        )
        self.ax_rssi.set_xlabel(
            "Temps relatif depuis le début (s)"
        )
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)

        self.ax_duree.set_title(
            "Durée des trames de l'appareil sélectionné"
        )
        self.ax_duree.set_xlabel(
            "Temps relatif depuis le début (s)"
        )
        self.ax_duree.set_ylabel("Durée (µs)")
        self.ax_duree.grid(True)

        self.ax_histogramme.set_title(
            "Histogramme des durées"
        )
        self.ax_histogramme.set_xlabel("Durée (µs)")
        self.ax_histogramme.set_ylabel(
            "Nombre de trames"
        )
        self.ax_histogramme.grid(True)

        self.ax_canaux.set_title(
            "Répartition des canaux"
        )
        self.ax_canaux.set_xlabel("Canal BLE")
        self.ax_canaux.set_ylabel(
            "Nombre de trames"
        )
        self.ax_canaux.set_xticks([37, 38, 39])
        self.ax_canaux.grid(True)

        self.canvas_temporel.draw_idle()
        self.canvas_distribution.draw_idle()

    @staticmethod
    def _sous_echantillonner(
        trames: list[BLEFrame],
    ) -> list[BLEFrame]:
        if len(trames) <= MAX_POINTS_GRAPHE:
            return trames

        pas = max(
            1,
            len(trames) // MAX_POINTS_GRAPHE,
        )
        resultat = trames[::pas]

        if resultat[-1] is not trames[-1]:
            resultat.append(trames[-1])

        return resultat

    def _rafraichir_graphe_temporel(self) -> None:
        self.ax_rssi.clear()
        self.ax_duree.clear()

        if self.trames:
            origine = self.trames[0].debut_s
            trames_tracees = self._sous_echantillonner(
                self.trames
            )

            temps = [
                trame.debut_s - origine
                for trame in trames_tracees
            ]

            temps_rssi: list[float] = []
            rssis: list[int] = []

            for temps_relatif, trame in zip(
                temps,
                trames_tracees,
            ):
                if trame.rssi_dbm is not None:
                    temps_rssi.append(temps_relatif)
                    rssis.append(trame.rssi_dbm)

            if rssis:
                self.ax_rssi.plot(
                    temps_rssi,
                    rssis,
                    marker=".",
                    markersize=3,
                    linewidth=0.8,
                )

            self.ax_duree.plot(
                temps,
                [
                    trame.duree_us
                    for trame in trames_tracees
                ],
                marker=".",
                markersize=3,
                linewidth=0.8,
            )

        self.ax_rssi.set_title(
            "RSSI de l'appareil sélectionné"
        )
        self.ax_rssi.set_xlabel(
            "Temps relatif depuis le début (s)"
        )
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)

        self.ax_duree.set_title(
            "Durée des trames de l'appareil sélectionné"
        )
        self.ax_duree.set_xlabel(
            "Temps relatif depuis le début (s)"
        )
        self.ax_duree.set_ylabel("Durée (µs)")
        self.ax_duree.grid(True)

        self.fig_temporel.tight_layout()
        self.canvas_temporel.draw_idle()

    def _rafraichir_graphe_distribution(self) -> None:
        self.ax_histogramme.clear()
        self.ax_canaux.clear()

        if self.trames:
            durees = [
                trame.duree_us
                for trame in self.trames
            ]

            self.ax_histogramme.hist(
                durees,
                bins=min(
                    30,
                    max(5, len(set(durees))),
                ),
            )

            compte = Counter(
                trame.canal
                for trame in self.trames
                if trame.canal in (37, 38, 39)
            )
            canaux = [37, 38, 39]
            valeurs = [
                compte.get(canal, 0)
                for canal in canaux
            ]

            self.ax_canaux.bar(canaux, valeurs)
            self.ax_canaux.set_xticks(canaux)

        self.ax_histogramme.set_title(
            "Histogramme des durées"
        )
        self.ax_histogramme.set_xlabel("Durée (µs)")
        self.ax_histogramme.set_ylabel(
            "Nombre de trames"
        )
        self.ax_histogramme.grid(True)

        self.ax_canaux.set_title(
            "Répartition des canaux"
        )
        self.ax_canaux.set_xlabel("Canal BLE")
        self.ax_canaux.set_ylabel(
            "Nombre de trames"
        )
        self.ax_canaux.set_xticks([37, 38, 39])
        self.ax_canaux.grid(True)

        self.fig_distribution.tight_layout()
        self.canvas_distribution.draw_idle()

    def _changement_onglet(
        self,
        _event: object = None,
    ) -> None:
        if not self.trames:
            return

        index = self.notebook.index(
            self.notebook.select()
        )

        if index == 2:
            self._rafraichir_graphe_temporel()
        elif index == 3:
            self._rafraichir_graphe_distribution()

    # =========================================================================
    # EXPORTS
    # =========================================================================

    def exporter_csv(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune trame n'a été enregistrée.",
            )
            return

        with CSV_FILTRE.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as fichier:
            writer = csv.writer(fichier)
            writer.writerow([
                "numero",
                "nom_appareil",
                "mac",
                "debut_s",
                "fin_s",
                "duree_us",
                "rssi_dbm",
                "canal",
                "payload_octets",
                "type_pdu",
                "phy",
            ])

            for trame in self.trames:
                writer.writerow([
                    trame.numero,
                    self.nom_selectionne,
                    trame.mac,
                    f"{trame.debut_s:.9f}",
                    f"{trame.fin_s:.9f}",
                    f"{trame.duree_us:.3f}",
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

        self.label_etat.config(
            text=f"CSV sauvegardé : {CSV_FILTRE}"
        )

    def exporter_statistiques(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune statistique disponible.",
            )
            return

        durees = [
            trame.duree_us
            for trame in self.trames
        ]
        rssis = [
            trame.rssi_dbm
            for trame in self.trames
            if trame.rssi_dbm is not None
        ]
        payloads = [
            trame.longueur_payload_octets
            for trame in self.trames
        ]
        compte = Counter(
            trame.canal
            for trame in self.trames
        )

        with CSV_STATS.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as fichier:
            writer = csv.writer(fichier)
            writer.writerow(["mesure", "valeur", "unite"])
            writer.writerow([
                "appareil",
                self.nom_selectionne,
                "",
            ])
            writer.writerow([
                "mac",
                self.mac_selectionnee,
                "",
            ])
            writer.writerow([
                "nombre_trames",
                len(self.trames),
                "trames",
            ])
            writer.writerow([
                "duree_moyenne",
                statistics.mean(durees),
                "us",
            ])
            writer.writerow([
                "duree_min",
                min(durees),
                "us",
            ])
            writer.writerow([
                "duree_max",
                max(durees),
                "us",
            ])
            writer.writerow([
                "payload_moyen",
                statistics.mean(payloads),
                "octets",
            ])

            if rssis:
                writer.writerow([
                    "rssi_moyen",
                    statistics.mean(rssis),
                    "dBm",
                ])
                writer.writerow([
                    "rssi_min",
                    min(rssis),
                    "dBm",
                ])
                writer.writerow([
                    "rssi_max",
                    max(rssis),
                    "dBm",
                ])

            for canal in (37, 38, 39):
                writer.writerow([
                    f"canal_{canal}",
                    compte.get(canal, 0),
                    "trames",
                ])

        self.label_etat.config(
            text=f"Statistiques sauvegardées : {CSV_STATS}"
        )

    def sauvegarder_graphe_temporel(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucun graphe disponible.",
            )
            return

        self._rafraichir_graphe_temporel()
        self.fig_temporel.savefig(
            PNG_TEMPOREL,
            dpi=300,
            bbox_inches="tight",
        )
        self.label_etat.config(
            text=f"Graphe 1 sauvegardé : {PNG_TEMPOREL}"
        )

    def sauvegarder_graphe_distribution(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucun graphe disponible.",
            )
            return

        self._rafraichir_graphe_distribution()
        self.fig_distribution.savefig(
            PNG_DISTRIBUTION,
            dpi=300,
            bbox_inches="tight",
        )
        self.label_etat.config(
            text=f"Graphe 2 sauvegardé : {PNG_DISTRIBUTION}"
        )

    def generer_rapport(self) -> None:
        if not self.trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucun rapport à générer.",
            )
            return

        durees = [
            trame.duree_us
            for trame in self.trames
        ]
        rssis = [
            trame.rssi_dbm
            for trame in self.trames
            if trame.rssi_dbm is not None
        ]
        payloads = [
            trame.longueur_payload_octets
            for trame in self.trames
        ]
        compte = Counter(
            trame.canal
            for trame in self.trames
        )

        with RAPPORT_TXT.open(
            "w",
            encoding="utf-8",
        ) as rapport:
            rapport.write(
                "RAPPORT D'ACQUISITION BLE\n"
            )
            rapport.write(
                "=========================\n\n"
            )
            rapport.write(
                f"Date : {datetime.now()}\n"
            )
            rapport.write(
                f"Appareil : {self.nom_selectionne}\n"
            )
            rapport.write(
                f"Adresse MAC : {self.mac_selectionnee}\n"
            )
            rapport.write(
                "Matériel : Adafruit Bluefruit LE "
                "Sniffer nRF51822\n"
            )
            rapport.write("PHY : LE 1M\n")
            rapport.write(
                f"Nombre de trames : "
                f"{len(self.trames)}\n\n"
            )

            rapport.write("DURÉE DES TRAMES\n")
            rapport.write("-----------------\n")
            rapport.write(
                f"Moyenne : "
                f"{statistics.mean(durees):.3f} µs\n"
            )
            rapport.write(
                f"Minimum : {min(durees):.3f} µs\n"
            )
            rapport.write(
                f"Maximum : {max(durees):.3f} µs\n\n"
            )

            rapport.write("PAYLOAD\n")
            rapport.write("-------\n")
            rapport.write(
                f"Moyenne : "
                f"{statistics.mean(payloads):.3f} octets\n"
            )
            rapport.write(
                f"Minimum : {min(payloads)} octets\n"
            )
            rapport.write(
                f"Maximum : {max(payloads)} octets\n\n"
            )

            if rssis:
                rapport.write("RSSI\n")
                rapport.write("----\n")
                rapport.write(
                    f"Moyenne : "
                    f"{statistics.mean(rssis):.2f} dBm\n"
                )
                rapport.write(
                    f"Minimum : {min(rssis)} dBm\n"
                )
                rapport.write(
                    f"Maximum : {max(rssis)} dBm\n\n"
                )

            rapport.write(
                "RÉPARTITION DES CANAUX\n"
            )
            rapport.write(
                "----------------------\n"
            )

            for canal in (37, 38, 39):
                rapport.write(
                    f"Canal {canal} : "
                    f"{compte.get(canal, 0)} trames\n"
                )

        self.label_etat.config(
            text=f"Rapport généré : {RAPPORT_TXT}"
        )

    # =========================================================================
    # RÉINITIALISATION
    # =========================================================================

    def reinitialiser(self) -> None:
        self.trames.clear()

        while True:
            try:
                self.file_trames.get_nowait()
            except queue.Empty:
                break

        for iid in self.table_trames.get_children():
            self.table_trames.delete(iid)

        valeurs_trame = {
            "numero": "Trame : ---",
            "appareil": "Appareil : ---",
            "mac": "MAC : ---",
            "rssi": "RSSI : ---",
            "canal": "Canal : ---",
            "payload": "Payload : ---",
            "duree": "Durée : ---",
            "type": "Type PDU : ---",
            "debut": "Début : ---",
            "fin": "Fin : ---",
        }

        for cle, valeur in valeurs_trame.items():
            self.vars_trame[cle].set(valeur)

        self.vars_stats["nombre"].set(
            "Nombre de trames : 0"
        )
        self.vars_stats["temps"].set(
            "Temps acquisition : 0,0 s"
        )
        self.vars_stats["duree_moy"].set(
            "Durée moyenne : ---"
        )
        self.vars_stats["duree_min"].set(
            "Durée min : ---"
        )
        self.vars_stats["duree_max"].set(
            "Durée max : ---"
        )
        self.vars_stats["rssi_moy"].set(
            "RSSI moyen : ---"
        )
        self.vars_stats["rssi_min"].set(
            "RSSI min : ---"
        )
        self.vars_stats["rssi_max"].set(
            "RSSI max : ---"
        )
        self.vars_stats["payload_moy"].set(
            "Payload moyen : ---"
        )
        self.vars_stats["debit"].set(
            "Débit : --- trames/s"
        )
        self.vars_stats["intervalle"].set(
            "Intervalle moyen : ---"
        )
        self.vars_stats["canaux"].set(
            "Canaux : 37=0, 38=0, 39=0"
        )

        self.ax_rssi.clear()
        self.ax_duree.clear()
        self.ax_histogramme.clear()
        self.ax_canaux.clear()
        self._configurer_graphes_vides()

        if not self.capture_active:
            self.label_etat.config(
                text="Mesures réinitialisées."
            )


# =============================================================================
# LANCEMENT
# =============================================================================

def main() -> None:
    fenetre = tk.Tk()
    InterfaceBLESelection(fenetre)
    fenetre.mainloop()


if __name__ == "__main__":
    main()
