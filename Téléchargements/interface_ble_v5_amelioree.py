#!/usr/bin/env python3
"""
interface_ble_v5.py

Analyseur BLE V5 pour Raspberry Pi + Adafruit Bluefruit LE Sniffer nRF51822.

Fonctions :
- capture BLE via ble_frame_driver_corrige.py ;
- détection automatique des appareils ;
- sélection et verrouillage d'un seul appareil ;
- tableau temps réel des trames ;
- RSSI, durée, payload, canal, type PDU ;
- courbe RSSI ;
- courbe durée ;
- histogramme des durées ;
- répartition des canaux ;
- chronologie temporelle des trames ;
- statistiques détaillées ;
- export CSV ;
- sauvegarde des graphes ;
- rapport TXT.

Le fichier ble_frame_driver_corrige.py doit être dans le même dossier.
"""

from __future__ import annotations

import csv
import queue
import statistics
import subprocess
import time
import tkinter as tk
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle

from ble_frame_driver_corrige import BLEFrame, BLESnifferDriver


# =============================================================================
# CONFIGURATION
# =============================================================================

DOSSIER_SORTIE = Path("acquisitions_ble_v5")
CSV_BRUT = DOSSIER_SORTIE / "trames_ble_brutes.csv"
CSV_FILTRE = DOSSIER_SORTIE / "trames_ble_filtrees.csv"
CSV_STATS = DOSSIER_SORTIE / "statistiques_ble.csv"
PNG_COMPLET = DOSSIER_SORTIE / "graphes_ble_v5.png"
PNG_CHRONO = DOSSIER_SORTIE / "chronologie_ble.png"
RAPPORT_TXT = DOSSIER_SORTIE / "rapport_ble_v5.txt"

PERIODE_MAJ_MS = 100
MAX_POINTS_TEMPS_REEL = 500
MAX_POINTS_TRACE_COMPLET = 2500
MAX_LIGNES_TABLEAU = 1000
TOUS_APPAREILS = "Tous les appareils"

FENETRES_TEMPORELLES = {
    "Temps réel (500 trames)": None,
    "10 dernières secondes": 10.0,
    "30 dernières secondes": 30.0,
    "60 dernières secondes": 60.0,
    "120 dernières secondes": 120.0,
    "Toute l'acquisition": -1.0,
}


# =============================================================================
# STRUCTURES
# =============================================================================

@dataclass
class AppareilBLE:
    mac: str
    nom: str = "Inconnu"
    nb_trames: int = 0
    dernier_rssi: Optional[int] = None
    dernier_canal: Optional[int] = None
    derniere_duree: Optional[float] = None
    rssis: list[int] = field(default_factory=list)
    durees: list[float] = field(default_factory=list)
    canaux: list[int] = field(default_factory=list)

    def ajouter(self, trame: BLEFrame) -> None:
        self.nb_trames += 1
        self.dernier_rssi = trame.rssi_dbm
        self.dernier_canal = trame.canal
        self.derniere_duree = trame.duree_us

        if trame.rssi_dbm is not None:
            self.rssis.append(trame.rssi_dbm)

        self.durees.append(trame.duree_us)

        if trame.canal is not None:
            self.canaux.append(trame.canal)

    @property
    def rssi_moyen(self) -> Optional[float]:
        return statistics.mean(self.rssis) if self.rssis else None

    @property
    def duree_moyenne(self) -> Optional[float]:
        return statistics.mean(self.durees) if self.durees else None


# =============================================================================
# APPLICATION
# =============================================================================

class AnalyseurBLEV5:
    def __init__(self, fenetre: tk.Tk) -> None:
        self.fenetre = fenetre
        self.fenetre.title("Analyseur BLE V5 - nRF51822")
        self.fenetre.geometry("1650x950")
        self.fenetre.minsize(1250, 780)

        DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

        self.driver: Optional[BLESnifferDriver] = None
        self.capture_active = False
        self.temps_debut_local: Optional[float] = None
        self.file_trames: queue.Queue[BLEFrame] = queue.Queue()

        self.toutes_trames: list[BLEFrame] = []
        self.trames_filtrees: list[BLEFrame] = []
        self.appareils: dict[str, AppareilBLE] = {}
        self.noms_connus = self._charger_noms_connus()

        self.mac_cible: Optional[str] = None
        self.filtre_verrouille = False

        # Affichage temporel :
        # pendant la capture, le mode temps réel limite le nombre de points ;
        # après l'arrêt, l'utilisateur peut afficher toute l'acquisition.
        self.var_fenetre_temps = tk.StringVar(
            value="Temps réel (500 trames)"
        )
        self.var_afficher_tout_arret = tk.BooleanVar(value=True)

        self._configurer_style()
        self._construire_interface()
        self._configurer_graphes_vides()

        self.fenetre.protocol("WM_DELETE_WINDOW", self.fermer)
        self.fenetre.after(PERIODE_MAJ_MS, self._traiter_file)

    # =========================================================================
    # STYLE
    # =========================================================================

    def _configurer_style(self) -> None:
        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Titre.TLabel", font=("Arial", 20, "bold"))
        style.configure("Valeur.TLabel", font=("Arial", 10, "bold"))
        style.configure("Treeview", rowheight=24)

    # =========================================================================
    # INTERFACE
    # =========================================================================

    def _construire_interface(self) -> None:
        principal = ttk.Frame(self.fenetre, padding=10)
        principal.pack(fill="both", expand=True)

        self._construire_panneau_gauche(principal)
        self._construire_onglets(principal)

    def _construire_panneau_gauche(self, parent: ttk.Frame) -> None:
        panneau = ttk.Frame(parent, width=330)
        panneau.pack(side="left", fill="y", padx=(0, 10))
        panneau.pack_propagate(False)

        ttk.Label(
            panneau,
            text="Analyseur BLE V5",
            style="Titre.TLabel",
        ).pack(pady=(0, 10))

        cadre_acq = ttk.LabelFrame(panneau, text="Acquisition", padding=10)
        cadre_acq.pack(fill="x", pady=5)

        self.btn_demarrer = ttk.Button(
            cadre_acq,
            text="Démarrer",
            command=self.demarrer,
        )
        self.btn_demarrer.pack(fill="x", pady=3)

        self.btn_arreter = ttk.Button(
            cadre_acq,
            text="Arrêter",
            command=self.arreter,
            state="disabled",
        )
        self.btn_arreter.pack(fill="x", pady=3)

        ttk.Button(
            cadre_acq,
            text="Réinitialiser",
            command=self.reinitialiser,
        ).pack(fill="x", pady=3)

        cadre_filtre = ttk.LabelFrame(panneau, text="Appareil BLE", padding=10)
        cadre_filtre.pack(fill="x", pady=5)

        self.var_appareil = tk.StringVar(value=TOUS_APPAREILS)

        self.combo_appareils = ttk.Combobox(
            cadre_filtre,
            textvariable=self.var_appareil,
            values=[TOUS_APPAREILS],
            state="readonly",
        )
        self.combo_appareils.pack(fill="x", pady=3)
        self.combo_appareils.bind("<<ComboboxSelected>>", self._selection_combo)

        self.btn_verrouiller = ttk.Button(
            cadre_filtre,
            text="Verrouiller l'appareil",
            command=self.verrouiller,
        )
        self.btn_verrouiller.pack(fill="x", pady=3)

        ttk.Button(
            cadre_filtre,
            text="Tous les appareils",
            command=self.afficher_tous,
        ).pack(fill="x", pady=3)

        self.label_filtre = ttk.Label(
            cadre_filtre,
            text="Filtre : tous les appareils",
            wraplength=280,
        )
        self.label_filtre.pack(anchor="w", pady=(5, 0))

        cadre_temps = ttk.LabelFrame(
            panneau,
            text="Fenêtre temporelle",
            padding=10,
        )
        cadre_temps.pack(fill="x", pady=5)

        self.combo_fenetre_temps = ttk.Combobox(
            cadre_temps,
            textvariable=self.var_fenetre_temps,
            values=list(FENETRES_TEMPORELLES.keys()),
            state="readonly",
        )
        self.combo_fenetre_temps.pack(fill="x", pady=3)
        self.combo_fenetre_temps.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._rafraichir_affichage_temporel(),
        )

        ttk.Checkbutton(
            cadre_temps,
            text="Afficher toute l'acquisition à l'arrêt",
            variable=self.var_afficher_tout_arret,
        ).pack(anchor="w", pady=3)

        self.label_plage = ttk.Label(
            cadre_temps,
            text="Plage affichée : aucune donnée",
            wraplength=280,
        )
        self.label_plage.pack(anchor="w", pady=(4, 0))

        cadre_export = ttk.LabelFrame(panneau, text="Export", padding=10)
        cadre_export.pack(fill="x", pady=5)

        ttk.Button(
            cadre_export,
            text="Exporter CSV filtré",
            command=self.exporter_csv,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Exporter statistiques",
            command=self.exporter_stats,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Sauvegarder tous les graphes",
            command=self.sauvegarder_graphes,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Sauvegarder chronologie",
            command=self.sauvegarder_chronologie,
        ).pack(fill="x", pady=3)

        ttk.Button(
            cadre_export,
            text="Générer rapport TXT",
            command=self.generer_rapport,
        ).pack(fill="x", pady=3)

        cadre_etat = ttk.LabelFrame(panneau, text="État", padding=10)
        cadre_etat.pack(fill="x", pady=5)

        self.label_etat = ttk.Label(
            cadre_etat,
            text="En attente",
            wraplength=280,
        )
        self.label_etat.pack(anchor="w")

        self.label_interface = ttk.Label(
            cadre_etat,
            text="Interface : auto",
            wraplength=280,
        )
        self.label_interface.pack(anchor="w", pady=(4, 0))

        self.label_csv = ttk.Label(
            cadre_etat,
            text=f"CSV brut : {CSV_BRUT}",
            wraplength=280,
        )
        self.label_csv.pack(anchor="w", pady=(4, 0))

    def _construire_onglets(self, parent: ttk.Frame) -> None:
        zone = ttk.Frame(parent)
        zone.pack(side="left", fill="both", expand=True)

        self.notebook = ttk.Notebook(zone)
        self.notebook.pack(fill="both", expand=True)

        self.onglet_vue = ttk.Frame(self.notebook)
        self.onglet_trames = ttk.Frame(self.notebook)
        self.onglet_graphes = ttk.Frame(self.notebook)
        self.onglet_chrono = ttk.Frame(self.notebook)

        self.notebook.add(self.onglet_vue, text="Vue générale")
        self.notebook.add(self.onglet_trames, text="Trames BLE")
        self.notebook.add(self.onglet_graphes, text="Analyse graphique")
        self.notebook.add(self.onglet_chrono, text="Chronologie")

        self._construire_vue_generale()
        self._construire_table_trames()
        self._construire_graphes()
        self._construire_chronologie()

    def _construire_vue_generale(self) -> None:
        cadre_appareils = ttk.LabelFrame(
            self.onglet_vue,
            text="Appareils détectés",
            padding=8,
        )
        cadre_appareils.pack(fill="x", padx=8, pady=8)

        colonnes = ("nom", "mac", "rssi", "canal", "trames", "duree")

        self.table_appareils = ttk.Treeview(
            cadre_appareils,
            columns=colonnes,
            show="headings",
            height=7,
        )

        titres = {
            "nom": "Nom",
            "mac": "MAC",
            "rssi": "RSSI",
            "canal": "Canal",
            "trames": "Nb trames",
            "duree": "Durée moyenne",
        }

        largeurs = {
            "nom": 160,
            "mac": 180,
            "rssi": 100,
            "canal": 80,
            "trames": 100,
            "duree": 130,
        }

        for col in colonnes:
            self.table_appareils.heading(col, text=titres[col])
            self.table_appareils.column(
                col,
                width=largeurs[col],
                anchor="center",
            )

        self.table_appareils.pack(fill="x", expand=True)
        self.table_appareils.bind(
            "<<TreeviewSelect>>",
            self._selection_table,
        )

        cadre_derniere = ttk.LabelFrame(
            self.onglet_vue,
            text="Dernière trame",
            padding=10,
        )
        cadre_derniere.pack(fill="x", padx=8, pady=8)

        self.vars_trame = {
            "numero": tk.StringVar(value="Trame : ---"),
            "nom": tk.StringVar(value="Appareil : ---"),
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
            ("nom", 0, 1),
            ("mac", 1, 0),
            ("rssi", 1, 1),
            ("canal", 2, 0),
            ("payload", 2, 1),
            ("duree", 3, 0),
            ("type", 3, 1),
            ("debut", 4, 0),
            ("fin", 4, 1),
        ]

        for nom, ligne, colonne in positions:
            ttk.Label(
                cadre_derniere,
                textvariable=self.vars_trame[nom],
                style="Valeur.TLabel",
            ).grid(
                row=ligne,
                column=colonne,
                sticky="w",
                padx=12,
                pady=3,
            )

        cadre_derniere.columnconfigure(0, weight=1)
        cadre_derniere.columnconfigure(1, weight=1)

        cadre_stats = ttk.LabelFrame(
            self.onglet_vue,
            text="Statistiques",
            padding=10,
        )
        cadre_stats.pack(fill="x", padx=8, pady=8)

        self.vars_stats = {
            "nb": tk.StringVar(value="Trames : 0"),
            "appareils": tk.StringVar(value="Appareils : 0"),
            "duree_moy": tk.StringVar(value="Durée moyenne : ---"),
            "duree_min": tk.StringVar(value="Durée min : ---"),
            "duree_max": tk.StringVar(value="Durée max : ---"),
            "rssi_moy": tk.StringVar(value="RSSI moyen : ---"),
            "rssi_min": tk.StringVar(value="RSSI min : ---"),
            "rssi_max": tk.StringVar(value="RSSI max : ---"),
            "payload_moy": tk.StringVar(value="Payload moyen : ---"),
            "debit": tk.StringVar(value="Débit : --- trames/s"),
            "intervalle": tk.StringVar(value="Intervalle moyen : ---"),
            "temps": tk.StringVar(value="Temps acquisition : 0,0 s"),
        }

        for i, var in enumerate(self.vars_stats.values()):
            ligne = i // 3
            colonne = i % 3
            ttk.Label(
                cadre_stats,
                textvariable=var,
            ).grid(
                row=ligne,
                column=colonne,
                sticky="w",
                padx=12,
                pady=3,
            )

        for col in range(3):
            cadre_stats.columnconfigure(col, weight=1)

    def _construire_table_trames(self) -> None:
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
            "rssi": "RSSI",
            "canal": "Canal",
            "payload": "Payload",
            "type": "Type PDU",
        }

        largeurs = {
            "numero": 65,
            "debut": 160,
            "fin": 160,
            "duree": 100,
            "mac": 150,
            "rssi": 80,
            "canal": 70,
            "payload": 90,
            "type": 100,
        }

        for col in colonnes:
            self.table_trames.heading(col, text=titres[col])
            self.table_trames.column(
                col,
                width=largeurs[col],
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

    def _construire_graphes(self) -> None:
        cadre = ttk.Frame(self.onglet_graphes, padding=8)
        cadre.pack(fill="both", expand=True)

        self.fig_graphes, axes = plt.subplots(2, 2, figsize=(11, 8))
        self.ax_rssi = axes[0][0]
        self.ax_duree = axes[0][1]
        self.ax_hist = axes[1][0]
        self.ax_canaux = axes[1][1]
        self.fig_graphes.subplots_adjust(hspace=0.42, wspace=0.28)

        self.canvas_graphes = FigureCanvasTkAgg(
            self.fig_graphes,
            master=cadre,
        )
        self.canvas_graphes.get_tk_widget().pack(fill="both", expand=True)

    def _construire_chronologie(self) -> None:
        cadre = ttk.Frame(self.onglet_chrono, padding=8)
        cadre.pack(fill="both", expand=True)

        self.fig_chrono, self.ax_chrono = plt.subplots(figsize=(12, 7))

        self.canvas_chrono = FigureCanvasTkAgg(
            self.fig_chrono,
            master=cadre,
        )
        self.canvas_chrono.get_tk_widget().pack(fill="both", expand=True)

    # =========================================================================
    # NOMS CONNUS
    # =========================================================================

    @staticmethod
    def _charger_noms_connus() -> dict[str, str]:
        noms: dict[str, str] = {}

        try:
            sortie = subprocess.check_output(
                ["bluetoothctl", "devices"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )

            for ligne in sortie.splitlines():
                parties = ligne.strip().split(maxsplit=2)

                if len(parties) >= 3 and parties[0] == "Device":
                    noms[parties[1].lower()] = parties[2]

        except Exception:
            pass

        return noms

    def _nom_appareil(self, mac: str) -> str:
        return self.noms_connus.get(mac.lower(), "Inconnu")

    # =========================================================================
    # ACQUISITION
    # =========================================================================

    def _callback_driver(self, trame: BLEFrame) -> None:
        self.file_trames.put(trame)

    def demarrer(self) -> None:
        if self.capture_active:
            return

        try:
            self.driver = BLESnifferDriver(
                interface=None,
                fichier_csv=CSV_BRUT,
                callback=self._callback_driver,
            )
            self.driver.demarrer()

            self.capture_active = True
            self.temps_debut_local = time.time()

            self.label_etat.config(text="Capture BLE en cours")
            self.label_interface.config(
                text=f"Interface : {self.driver.interface}"
            )

            self.btn_demarrer.config(state="disabled")
            self.btn_arreter.config(state="normal")

        except Exception as erreur:
            messagebox.showerror("Erreur", str(erreur))
            self.label_etat.config(text=f"Erreur : {erreur}")

    def arreter(self) -> None:
        if self.driver is not None:
            self.driver.arreter()

        self.driver = None
        self.capture_active = False

        self.btn_demarrer.config(state="normal")
        self.btn_arreter.config(state="disabled")
        self.label_etat.config(text="Capture arrêtée")

        if self.var_afficher_tout_arret.get() and self.trames_filtrees:
            self.var_fenetre_temps.set("Toute l'acquisition")
            self._rafraichir_affichage_temporel()

    def fermer(self) -> None:
        self.arreter()
        self.fenetre.destroy()

    def _traiter_file(self) -> None:
        nouvelles = 0

        while True:
            try:
                trame = self.file_trames.get_nowait()
            except queue.Empty:
                break

            self._ajouter_trame(trame)
            nouvelles += 1

        if nouvelles:
            self._rafraichir_appareils()
            self._recalculer_filtre()
            self._rafraichir_table_trames()
            self._rafraichir_stats()
            self._rafraichir_graphes()
            self._rafraichir_chronologie()

        if self.capture_active and self.temps_debut_local is not None:
            duree = time.time() - self.temps_debut_local
            self.vars_stats["temps"].set(
                f"Temps acquisition : {duree:.1f} s"
            )

        self.fenetre.after(PERIODE_MAJ_MS, self._traiter_file)

    def _ajouter_trame(self, trame: BLEFrame) -> None:
        self.toutes_trames.append(trame)

        cle = trame.mac.lower()

        if cle not in self.appareils:
            self.appareils[cle] = AppareilBLE(
                mac=trame.mac,
                nom=self._nom_appareil(trame.mac),
            )

        self.appareils[cle].ajouter(trame)

        if self._trame_passe_filtre(trame):
            self._afficher_derniere_trame(trame)

    # =========================================================================
    # FILTRE
    # =========================================================================

    def _trame_passe_filtre(self, trame: BLEFrame) -> bool:
        return (
            self.mac_cible is None
            or trame.mac.lower() == self.mac_cible.lower()
        )

    def _selection_combo(self, _event: object = None) -> None:
        selection = self.var_appareil.get()

        if selection == TOUS_APPAREILS:
            self.mac_cible = None
            self.filtre_verrouille = False
            self.label_filtre.config(text="Filtre : tous les appareils")
        else:
            morceaux = selection.rsplit("|", maxsplit=1)
            if len(morceaux) == 2:
                self.mac_cible = morceaux[1].strip().lower()
                self.label_filtre.config(
                    text=f"Filtre sélectionné : {self.mac_cible}"
                )

        self._appliquer_filtre_et_rafraichir()

    def _selection_table(self, _event: object = None) -> None:
        selection = self.table_appareils.selection()

        if not selection:
            return

        iid = selection[0]
        valeurs = self.table_appareils.item(iid, "values")

        if len(valeurs) < 2:
            return

        mac = str(valeurs[1]).lower()
        appareil = self.appareils.get(mac)

        if appareil is None:
            return

        self.var_appareil.set(f"{appareil.nom} | {appareil.mac}")

        if not self.filtre_verrouille:
            self.mac_cible = mac
            self.label_filtre.config(
                text=f"Filtre sélectionné : {appareil.mac}"
            )
            self._appliquer_filtre_et_rafraichir()

    def verrouiller(self) -> None:
        if self.var_appareil.get() == TOUS_APPAREILS:
            messagebox.showwarning(
                "Sélection",
                "Sélectionne un appareil avant de le verrouiller.",
            )
            return

        self._selection_combo()
        self.filtre_verrouille = True
        self.btn_verrouiller.config(text="Appareil verrouillé")
        self.label_filtre.config(
            text=f"Appareil verrouillé : {self.mac_cible}"
        )

    def afficher_tous(self) -> None:
        self.mac_cible = None
        self.filtre_verrouille = False
        self.var_appareil.set(TOUS_APPAREILS)
        self.btn_verrouiller.config(text="Verrouiller l'appareil")
        self.label_filtre.config(text="Filtre : tous les appareils")
        self._appliquer_filtre_et_rafraichir()

    def _appliquer_filtre_et_rafraichir(self) -> None:
        self._recalculer_filtre()
        self._rafraichir_table_trames()
        self._rafraichir_stats()
        self._rafraichir_graphes()
        self._rafraichir_chronologie()

    def _recalculer_filtre(self) -> None:
        if self.mac_cible is None:
            self.trames_filtrees = list(self.toutes_trames)
        else:
            cible = self.mac_cible.lower()
            self.trames_filtrees = [
                t for t in self.toutes_trames
                if t.mac.lower() == cible
            ]

        if self.trames_filtrees:
            self._afficher_derniere_trame(self.trames_filtrees[-1])

    # =========================================================================
    # AFFICHAGE
    # =========================================================================

    def _rafraichir_appareils(self) -> None:
        selection_actuelle = self.table_appareils.selection()

        for iid in self.table_appareils.get_children():
            self.table_appareils.delete(iid)

        valeurs_combo = [TOUS_APPAREILS]

        appareils_tries = sorted(
            self.appareils.values(),
            key=lambda a: a.nb_trames,
            reverse=True,
        )

        for appareil in appareils_tries:
            rssi = (
                "---"
                if appareil.dernier_rssi is None
                else f"{appareil.dernier_rssi} dBm"
            )
            canal = (
                "---"
                if appareil.dernier_canal is None
                else str(appareil.dernier_canal)
            )
            duree = (
                "---"
                if appareil.duree_moyenne is None
                else f"{appareil.duree_moyenne:.1f} µs"
            )

            iid = appareil.mac.lower()

            self.table_appareils.insert(
                "",
                "end",
                iid=iid,
                values=(
                    appareil.nom,
                    appareil.mac,
                    rssi,
                    canal,
                    appareil.nb_trames,
                    duree,
                ),
            )

            valeurs_combo.append(f"{appareil.nom} | {appareil.mac}")

        self.combo_appareils.configure(values=valeurs_combo)

        if selection_actuelle:
            iid = selection_actuelle[0]
            if iid in self.table_appareils.get_children():
                self.table_appareils.selection_set(iid)

        self.vars_stats["appareils"].set(
            f"Appareils : {len(self.appareils)}"
        )

    def _afficher_derniere_trame(self, trame: BLEFrame) -> None:
        self.vars_trame["numero"].set(f"Trame : {trame.numero}")
        self.vars_trame["nom"].set(
            f"Appareil : {self._nom_appareil(trame.mac)}"
        )
        self.vars_trame["mac"].set(f"MAC : {trame.mac}")
        self.vars_trame["rssi"].set(
            f"RSSI : {trame.rssi_dbm if trame.rssi_dbm is not None else '---'} dBm"
        )
        self.vars_trame["canal"].set(
            f"Canal : {trame.canal if trame.canal is not None else '---'}"
        )
        self.vars_trame["payload"].set(
            f"Payload : {trame.longueur_payload_octets} octets"
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

    def _rafraichir_table_trames(self) -> None:
        for iid in self.table_trames.get_children():
            self.table_trames.delete(iid)

        lignes = self.trames_filtrees[-MAX_LIGNES_TABLEAU:]

        for trame in lignes:
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
                        else f"{trame.rssi_dbm}"
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

    def _rafraichir_stats(self) -> None:
        trames = self.trames_filtrees

        self.vars_stats["nb"].set(f"Trames : {len(trames)}")

        if not trames:
            return

        durees = [t.duree_us for t in trames]
        rssis = [t.rssi_dbm for t in trames if t.rssi_dbm is not None]
        payloads = [t.longueur_payload_octets for t in trames]

        self.vars_stats["duree_moy"].set(
            f"Durée moyenne : {statistics.mean(durees):.1f} µs"
        )
        self.vars_stats["duree_min"].set(
            f"Durée min : {min(durees):.1f} µs"
        )
        self.vars_stats["duree_max"].set(
            f"Durée max : {max(durees):.1f} µs"
        )
        self.vars_stats["payload_moy"].set(
            f"Payload moyen : {statistics.mean(payloads):.1f} octets"
        )

        if rssis:
            self.vars_stats["rssi_moy"].set(
                f"RSSI moyen : {statistics.mean(rssis):.1f} dBm"
            )
            self.vars_stats["rssi_min"].set(
                f"RSSI min : {min(rssis)} dBm"
            )
            self.vars_stats["rssi_max"].set(
                f"RSSI max : {max(rssis)} dBm"
            )

        if len(trames) >= 2:
            duree_totale = trames[-1].debut_s - trames[0].debut_s

            if duree_totale > 0:
                debit = (len(trames) - 1) / duree_totale
                self.vars_stats["debit"].set(
                    f"Débit : {debit:.2f} trames/s"
                )

            intervalles = [
                trames[i].debut_s - trames[i - 1].debut_s
                for i in range(1, len(trames))
            ]

            self.vars_stats["intervalle"].set(
                f"Intervalle moyen : {statistics.mean(intervalles):.4f} s"
            )

    # =========================================================================
    # SÉLECTION DE LA PLAGE TEMPORELLE
    # =========================================================================

    def _rafraichir_affichage_temporel(self) -> None:
        self._rafraichir_graphes()
        self._rafraichir_chronologie()

    def _trames_pour_affichage(self) -> list[BLEFrame]:
        """
        Retourne les trames correspondant à la fenêtre choisie.

        Point important :
        le temps relatif reste calculé depuis le début réel de
        l'acquisition filtrée. Ainsi, une fenêtre affichant les dernières
        30 secondes d'une acquisition de 2 minutes porte sur l'axe
        approximativement 90 à 120 s, et non 0 à 30 s.
        """
        if not self.trames_filtrees:
            self.label_plage.config(
                text="Plage affichée : aucune donnée"
            )
            return []

        choix = self.var_fenetre_temps.get()
        valeur = FENETRES_TEMPORELLES.get(choix)

        if valeur == -1.0:
            trames = list(self.trames_filtrees)
        elif valeur is None:
            trames = self.trames_filtrees[-MAX_POINTS_TEMPS_REEL:]
        else:
            fin = self.trames_filtrees[-1].debut_s
            debut = fin - valeur
            trames = [
                trame
                for trame in self.trames_filtrees
                if trame.debut_s >= debut
            ]

        origine_globale = self.trames_filtrees[0].debut_s
        debut_rel = trames[0].debut_s - origine_globale
        fin_rel = trames[-1].fin_s - origine_globale

        self.label_plage.config(
            text=(
                f"Plage affichée : {debut_rel:.1f} à "
                f"{fin_rel:.1f} s — {len(trames)} trames"
            )
        )
        return trames

    @staticmethod
    def _echantillonner(
        trames: list[BLEFrame],
        maximum: int,
    ) -> list[BLEFrame]:
        """
        Réduit uniquement le nombre de points dessinés.
        Les statistiques et les fichiers CSV restent calculés sur toutes
        les trames.
        """
        if len(trames) <= maximum:
            return trames

        pas = max(1, len(trames) // maximum)
        resultat = trames[::pas]

        if resultat[-1] is not trames[-1]:
            resultat.append(trames[-1])

        return resultat

    # =========================================================================
    # GRAPHES
    # =========================================================================

    def _configurer_graphes_vides(self) -> None:
        self.ax_rssi.set_title("RSSI")
        self.ax_rssi.set_xlabel("Temps relatif depuis le début (s)")
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)

        self.ax_duree.set_title("Durée des trames")
        self.ax_duree.set_xlabel("Temps relatif depuis le début (s)")
        self.ax_duree.set_ylabel("Durée (µs)")
        self.ax_duree.grid(True)

        self.ax_hist.set_title("Histogramme des durées")
        self.ax_hist.set_xlabel("Durée (µs)")
        self.ax_hist.set_ylabel("Nombre de trames")
        self.ax_hist.grid(True)

        self.ax_canaux.set_title("Répartition des canaux")
        self.ax_canaux.set_xlabel("Canal")
        self.ax_canaux.set_ylabel("Nombre de trames")
        self.ax_canaux.grid(True)

        self.ax_chrono.set_title("Chronologie des trames BLE")
        self.ax_chrono.set_xlabel("Temps relatif (s)")
        self.ax_chrono.set_ylabel("Canal")
        self.ax_chrono.set_yticks([37, 38, 39])
        self.ax_chrono.grid(True)

        self.canvas_graphes.draw_idle()
        self.canvas_chrono.draw_idle()

    def _rafraichir_graphes(self) -> None:
        self.ax_rssi.clear()
        self.ax_duree.clear()
        self.ax_hist.clear()
        self.ax_canaux.clear()

        trames_plage = self._trames_pour_affichage()
        trames_trace = self._echantillonner(
            trames_plage,
            MAX_POINTS_TRACE_COMPLET,
        )

        if trames_plage:
            # Origine globale : le temps ne repart plus à zéro lorsque
            # l'interface n'affiche que les dernières trames.
            origine = self.trames_filtrees[0].debut_s

            temps_trace = [
                trame.debut_s - origine
                for trame in trames_trace
            ]
            durees_trace = [
                trame.duree_us
                for trame in trames_trace
            ]

            temps_rssi: list[float] = []
            rssis: list[int] = []

            for temps_relatif, trame in zip(
                temps_trace,
                trames_trace,
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
                temps_trace,
                durees_trace,
                marker=".",
                markersize=3,
                linewidth=0.8,
            )

            # Histogramme et canaux : toutes les trames de la plage,
            # et pas uniquement les points sous-échantillonnés.
            durees_completes = [
                trame.duree_us
                for trame in trames_plage
            ]
            self.ax_hist.hist(
                durees_completes,
                bins=min(
                    30,
                    max(5, len(set(durees_completes))),
                ),
            )

            compte = Counter(
                trame.canal
                for trame in trames_plage
                if trame.canal in (37, 38, 39)
            )
            canaux = [37, 38, 39]
            valeurs = [compte.get(canal, 0) for canal in canaux]
            self.ax_canaux.bar(canaux, valeurs)
            self.ax_canaux.set_xticks(canaux)

            debut_x = trames_plage[0].debut_s - origine
            fin_x = trames_plage[-1].fin_s - origine
            marge = max((fin_x - debut_x) * 0.02, 0.05)
            self.ax_rssi.set_xlim(
                max(0.0, debut_x - marge),
                fin_x + marge,
            )
            self.ax_duree.set_xlim(
                max(0.0, debut_x - marge),
                fin_x + marge,
            )

        self._configurer_graphes_vides()

    def _rafraichir_chronologie(self) -> None:
        self.ax_chrono.clear()

        trames_plage = self._trames_pour_affichage()

        # La chronologie est limitée visuellement pour que l'interface
        # reste fluide. La plage temporelle reste correcte.
        trames = self._echantillonner(trames_plage, 800)

        if trames_plage:
            origine = self.trames_filtrees[0].debut_s

            for trame in trames:
                if trame.canal not in (37, 38, 39):
                    continue

                debut_rel = trame.debut_s - origine
                largeur_reelle = trame.duree_us / 1_000_000.0

                # Rectangle à la largeur radio réelle.
                rectangle = Rectangle(
                    (debut_rel, trame.canal - 0.20),
                    largeur_reelle,
                    0.40,
                )
                self.ax_chrono.add_patch(rectangle)

                # Marqueur vertical pour que les trames restent visibles
                # lorsque l'axe couvre plusieurs secondes.
                self.ax_chrono.vlines(
                    debut_rel,
                    trame.canal - 0.28,
                    trame.canal + 0.28,
                    linewidth=0.6,
                )

            debut_rel = trames_plage[0].debut_s - origine
            fin_rel = trames_plage[-1].fin_s - origine
            marge = max((fin_rel - debut_rel) * 0.02, 0.05)
            self.ax_chrono.set_xlim(
                max(0.0, debut_rel - marge),
                fin_rel + marge,
            )

        self.ax_chrono.set_title(
            "Chronologie des trames BLE "
            "(rectangle réel + marqueur de visibilité)"
        )
        self.ax_chrono.set_xlabel(
            "Temps relatif depuis le début de l'acquisition (s)"
        )
        self.ax_chrono.set_ylabel("Canal")
        self.ax_chrono.set_yticks([37, 38, 39])
        self.ax_chrono.set_ylim(36.5, 39.5)
        self.ax_chrono.grid(True)
        self.canvas_chrono.draw_idle()

    # =========================================================================
    # EXPORT
    # =========================================================================

    def exporter_csv(self) -> None:
        if not self.trames_filtrees:
            messagebox.showwarning("Aucune donnée", "Aucune trame à exporter.")
            return

        with CSV_FILTRE.open("w", newline="", encoding="utf-8") as fichier:
            writer = csv.writer(fichier)
            writer.writerow([
                "numero",
                "nom",
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

            for t in self.trames_filtrees:
                writer.writerow([
                    t.numero,
                    self._nom_appareil(t.mac),
                    t.mac,
                    f"{t.debut_s:.9f}",
                    f"{t.fin_s:.9f}",
                    f"{t.duree_us:.3f}",
                    "" if t.rssi_dbm is None else t.rssi_dbm,
                    "" if t.canal is None else t.canal,
                    t.longueur_payload_octets,
                    t.type_pdu,
                    t.phy,
                ])

        self.label_etat.config(text=f"CSV exporté : {CSV_FILTRE}")

    def exporter_stats(self) -> None:
        if not self.trames_filtrees:
            messagebox.showwarning("Aucune donnée", "Aucune statistique à exporter.")
            return

        trames = self.trames_filtrees
        durees = [t.duree_us for t in trames]
        rssis = [t.rssi_dbm for t in trames if t.rssi_dbm is not None]
        payloads = [t.longueur_payload_octets for t in trames]
        compte_canaux = Counter(t.canal for t in trames)

        with CSV_STATS.open("w", newline="", encoding="utf-8") as fichier:
            writer = csv.writer(fichier)
            writer.writerow(["mesure", "valeur", "unite"])
            writer.writerow(["nombre_trames", len(trames), "trames"])
            writer.writerow(["duree_moyenne", statistics.mean(durees), "us"])
            writer.writerow(["duree_min", min(durees), "us"])
            writer.writerow(["duree_max", max(durees), "us"])
            writer.writerow(["payload_moyen", statistics.mean(payloads), "octets"])

            if rssis:
                writer.writerow(["rssi_moyen", statistics.mean(rssis), "dBm"])
                writer.writerow(["rssi_min", min(rssis), "dBm"])
                writer.writerow(["rssi_max", max(rssis), "dBm"])

            for canal in (37, 38, 39):
                writer.writerow([
                    f"nombre_trames_canal_{canal}",
                    compte_canaux.get(canal, 0),
                    "trames",
                ])

        self.label_etat.config(text=f"Statistiques exportées : {CSV_STATS}")

    def sauvegarder_graphes(self) -> None:
        if not self.trames_filtrees:
            messagebox.showwarning("Aucune donnée", "Aucun graphe à sauvegarder.")
            return

        self.fig_graphes.savefig(
            PNG_COMPLET,
            dpi=300,
            bbox_inches="tight",
        )
        self.label_etat.config(text=f"Graphes sauvegardés : {PNG_COMPLET}")

    def sauvegarder_chronologie(self) -> None:
        if not self.trames_filtrees:
            messagebox.showwarning("Aucune donnée", "Aucune chronologie à sauvegarder.")
            return

        self.fig_chrono.savefig(
            PNG_CHRONO,
            dpi=300,
            bbox_inches="tight",
        )
        self.label_etat.config(text=f"Chronologie sauvegardée : {PNG_CHRONO}")

    def generer_rapport(self) -> None:
        if not self.trames_filtrees:
            messagebox.showwarning("Aucune donnée", "Aucun rapport à générer.")
            return

        trames = self.trames_filtrees
        durees = [t.duree_us for t in trames]
        rssis = [t.rssi_dbm for t in trames if t.rssi_dbm is not None]
        payloads = [t.longueur_payload_octets for t in trames]
        compte_canaux = Counter(t.canal for t in trames)

        filtre = TOUS_APPAREILS if self.mac_cible is None else self.mac_cible

        with RAPPORT_TXT.open("w", encoding="utf-8") as rapport:
            rapport.write("RAPPORT D'ACQUISITION BLE V5\n")
            rapport.write("============================\n\n")
            rapport.write(f"Date : {datetime.now()}\n")
            rapport.write("Matériel : Adafruit Bluefruit LE Sniffer nRF51822\n")
            rapport.write("PHY : LE 1M\n")
            rapport.write(f"Filtre : {filtre}\n")
            rapport.write(f"Nombre de trames : {len(trames)}\n")
            rapport.write(f"Nombre d'appareils détectés : {len(self.appareils)}\n\n")

            rapport.write("DURÉE DES TRAMES\n")
            rapport.write("-----------------\n")
            rapport.write(f"Moyenne : {statistics.mean(durees):.3f} µs\n")
            rapport.write(f"Minimum : {min(durees):.3f} µs\n")
            rapport.write(f"Maximum : {max(durees):.3f} µs\n\n")

            rapport.write("PAYLOAD\n")
            rapport.write("-------\n")
            rapport.write(f"Moyenne : {statistics.mean(payloads):.3f} octets\n")
            rapport.write(f"Minimum : {min(payloads)} octets\n")
            rapport.write(f"Maximum : {max(payloads)} octets\n\n")

            if rssis:
                rapport.write("RSSI\n")
                rapport.write("----\n")
                rapport.write(f"Moyenne : {statistics.mean(rssis):.2f} dBm\n")
                rapport.write(f"Minimum : {min(rssis)} dBm\n")
                rapport.write(f"Maximum : {max(rssis)} dBm\n\n")

            rapport.write("RÉPARTITION PAR CANAL\n")
            rapport.write("---------------------\n")
            for canal in (37, 38, 39):
                rapport.write(
                    f"Canal {canal} : {compte_canaux.get(canal, 0)} trames\n"
                )

        self.label_etat.config(text=f"Rapport généré : {RAPPORT_TXT}")

    # =========================================================================
    # RÉINITIALISATION
    # =========================================================================

    def reinitialiser(self) -> None:
        self.toutes_trames.clear()
        self.trames_filtrees.clear()
        self.appareils.clear()
        self.mac_cible = None
        self.filtre_verrouille = False

        self.var_appareil.set(TOUS_APPAREILS)
        self.combo_appareils.configure(values=[TOUS_APPAREILS])
        self.var_fenetre_temps.set("Temps réel (500 trames)")
        self.label_plage.config(
            text="Plage affichée : aucune donnée"
        )
        self.btn_verrouiller.config(text="Verrouiller l'appareil")
        self.label_filtre.config(text="Filtre : tous les appareils")

        for table in (self.table_appareils, self.table_trames):
            for iid in table.get_children():
                table.delete(iid)

        self.vars_stats["nb"].set("Trames : 0")
        self.vars_stats["appareils"].set("Appareils : 0")
        self.vars_stats["duree_moy"].set("Durée moyenne : ---")
        self.vars_stats["duree_min"].set("Durée min : ---")
        self.vars_stats["duree_max"].set("Durée max : ---")
        self.vars_stats["rssi_moy"].set("RSSI moyen : ---")
        self.vars_stats["rssi_min"].set("RSSI min : ---")
        self.vars_stats["rssi_max"].set("RSSI max : ---")
        self.vars_stats["payload_moy"].set("Payload moyen : ---")
        self.vars_stats["debit"].set("Débit : --- trames/s")
        self.vars_stats["intervalle"].set("Intervalle moyen : ---")
        self.vars_stats["temps"].set("Temps acquisition : 0,0 s")

        for var in self.vars_trame.values():
            var.set("---")

        self.ax_rssi.clear()
        self.ax_duree.clear()
        self.ax_hist.clear()
        self.ax_canaux.clear()
        self.ax_chrono.clear()
        self._configurer_graphes_vides()

        self.label_etat.config(text="Réinitialisation effectuée")


# =============================================================================
# LANCEMENT
# =============================================================================

def main() -> None:
    fenetre = tk.Tk()
    AnalyseurBLEV5(fenetre)
    fenetre.mainloop()


if __name__ == "__main__":
    main()
