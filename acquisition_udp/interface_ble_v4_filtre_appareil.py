#!/usr/bin/env python3
"""
interface_ble_v4.py

Interface graphique BLE complète avec :
- capture via nRF51822 + TShark ;
- détection automatique des appareils BLE ;
- tableau des appareils détectés ;
- sélection d'un seul appareil ;
- mode "Tous les appareils" ;
- verrouillage/déverrouillage du filtre ;
- affichage temps réel RSSI, canal, longueur, durée, début et fin ;
- courbes RSSI et durée des trames ;
- statistiques globales ou filtrées ;
- CSV brut automatique via le driver ;
- CSV filtré exportable ;
- sauvegarde PNG ;
- rapport TXT.

Le fichier suivant doit se trouver dans le même dossier :
    ble_frame_driver_corrige.py
"""

from __future__ import annotations

import csv
import queue
import statistics
import subprocess
import time
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass, field
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

DOSSIER_SORTIE = Path("acquisitions_ble_v4")
CSV_BRUT = DOSSIER_SORTIE / "trames_ble_brutes.csv"
CSV_FILTRE = DOSSIER_SORTIE / "trames_ble_filtrees.csv"
PNG_GRAPHES = DOSSIER_SORTIE / "graphes_ble.png"
RAPPORT_TXT = DOSSIER_SORTIE / "rapport_ble.txt"

PERIODE_MAJ_MS = 100
MAX_POINTS_AFFICHES = 400
TOUS_APPAREILS = "Tous les appareils"


# =============================================================================
# DONNÉES PAR APPAREIL
# =============================================================================

@dataclass
class StatistiquesAppareil:
    mac: str
    nom: str = "Inconnu"
    nb_trames: int = 0
    dernier_rssi: Optional[int] = None
    dernier_canal: Optional[int] = None
    derniere_duree_us: Optional[float] = None
    premiere_detection_s: Optional[float] = None
    derniere_detection_s: Optional[float] = None
    durees: list[float] = field(default_factory=list)
    rssis: list[int] = field(default_factory=list)

    def ajouter(self, trame: BLEFrame) -> None:
        self.nb_trames += 1
        self.dernier_rssi = trame.rssi_dbm
        self.dernier_canal = trame.canal
        self.derniere_duree_us = trame.duree_us

        if self.premiere_detection_s is None:
            self.premiere_detection_s = trame.debut_s

        self.derniere_detection_s = trame.debut_s
        self.durees.append(trame.duree_us)

        if trame.rssi_dbm is not None:
            self.rssis.append(trame.rssi_dbm)

    @property
    def duree_moyenne(self) -> Optional[float]:
        if not self.durees:
            return None
        return statistics.mean(self.durees)

    @property
    def rssi_moyen(self) -> Optional[float]:
        if not self.rssis:
            return None
        return statistics.mean(self.rssis)


# =============================================================================
# APPLICATION
# =============================================================================

class InterfaceBLEV4:
    def __init__(self, fenetre: tk.Tk) -> None:
        self.fenetre = fenetre
        self.fenetre.title("Analyseur BLE V4 - nRF51822")
        self.fenetre.geometry("1550x900")
        self.fenetre.minsize(1200, 760)

        DOSSIER_SORTIE.mkdir(parents=True, exist_ok=True)

        self.driver: Optional[BLESnifferDriver] = None
        self.capture_active = False
        self.file_trames: queue.Queue[BLEFrame] = queue.Queue()
        self.temps_demarrage_local: Optional[float] = None

        self.toutes_trames: list[BLEFrame] = []
        self.trames_affichees: list[BLEFrame] = []
        self.appareils: dict[str, StatistiquesAppareil] = {}
        self.noms_connus = self._charger_noms_bluetoothctl()

        self.mac_cible: Optional[str] = None
        self.filtre_verrouille = False

        self._configurer_style()
        self._construire_interface()
        self._configurer_graphes()

        self.fenetre.protocol("WM_DELETE_WINDOW", self.fermer)
        self.fenetre.after(PERIODE_MAJ_MS, self._traiter_file_trames)

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
        style.configure("SousTitre.TLabel", font=("Arial", 12, "bold"))
        style.configure("Valeur.TLabel", font=("Arial", 11, "bold"))
        style.configure("Etat.TLabel", font=("Arial", 10))
        style.configure("Treeview", rowheight=25)

    # =========================================================================
    # CONSTRUCTION DE L'INTERFACE
    # =========================================================================

    def _construire_interface(self) -> None:
        conteneur = ttk.Frame(self.fenetre, padding=10)
        conteneur.pack(fill="both", expand=True)

        self._construire_panneau_gauche(conteneur)
        self._construire_zone_droite(conteneur)

    def _construire_panneau_gauche(self, parent: ttk.Frame) -> None:
        panneau = ttk.Frame(parent, width=380)
        panneau.pack(side="left", fill="y", padx=(0, 10))
        panneau.pack_propagate(False)

        ttk.Label(
            panneau,
            text="Analyseur BLE V4",
            style="Titre.TLabel",
        ).pack(pady=(0, 10))

        # Acquisition
        cadre_acquisition = ttk.LabelFrame(
            panneau,
            text="Acquisition",
            padding=10,
        )
        cadre_acquisition.pack(fill="x", pady=5)

        self.bouton_demarrer = ttk.Button(
            cadre_acquisition,
            text="Démarrer la capture",
            command=self.demarrer,
        )
        self.bouton_demarrer.pack(fill="x", pady=3)

        self.bouton_arreter = ttk.Button(
            cadre_acquisition,
            text="Arrêter la capture",
            command=self.arreter,
            state="disabled",
        )
        self.bouton_arreter.pack(fill="x", pady=3)

        ttk.Button(
            cadre_acquisition,
            text="Réinitialiser l'affichage",
            command=self.reinitialiser_affichage,
        ).pack(fill="x", pady=3)

        # Filtre appareil
        cadre_filtre = ttk.LabelFrame(
            panneau,
            text="Filtre appareil BLE",
            padding=10,
        )
        cadre_filtre.pack(fill="x", pady=5)

        self.var_selection = tk.StringVar(value=TOUS_APPAREILS)

        self.combo_appareils = ttk.Combobox(
            cadre_filtre,
            textvariable=self.var_selection,
            values=[TOUS_APPAREILS],
            state="readonly",
        )
        self.combo_appareils.pack(fill="x", pady=3)
        self.combo_appareils.bind(
            "<<ComboboxSelected>>",
            self._sur_selection_combo,
        )

        self.bouton_verrouiller = ttk.Button(
            cadre_filtre,
            text="Verrouiller cet appareil",
            command=self.verrouiller_appareil,
        )
        self.bouton_verrouiller.pack(fill="x", pady=3)

        ttk.Button(
            cadre_filtre,
            text="Afficher tous les appareils",
            command=self.afficher_tous,
        ).pack(fill="x", pady=3)

        self.label_filtre = ttk.Label(
            cadre_filtre,
            text="Filtre : tous les appareils",
            wraplength=330,
        )
        self.label_filtre.pack(anchor="w", pady=(5, 0))

        # Sauvegarde
        cadre_sauvegarde = ttk.LabelFrame(
            panneau,
            text="Sauvegardes",
            padding=10,
        )
        cadre_sauvegarde.pack(fill="x", pady=5)

        ttk.Button(
            cadre_sauvegarde,
            text="Exporter CSV filtré",
            command=self.exporter_csv_filtre,
        ).pack(fill="x", pady=3)

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

        # État
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
            wraplength=330,
        )
        self.label_etat.pack(anchor="w")

        self.label_interface = ttk.Label(
            cadre_etat,
            text="Interface : détection automatique",
            wraplength=330,
        )
        self.label_interface.pack(anchor="w", pady=(4, 0))

        self.label_csv = ttk.Label(
            cadre_etat,
            text=f"CSV brut : {CSV_BRUT}",
            wraplength=330,
        )
        self.label_csv.pack(anchor="w", pady=(4, 0))

    def _construire_zone_droite(self, parent: ttk.Frame) -> None:
        droite = ttk.Frame(parent)
        droite.pack(side="left", fill="both", expand=True)

        # Tableau des appareils
        cadre_appareils = ttk.LabelFrame(
            droite,
            text="Appareils BLE détectés",
            padding=8,
        )
        cadre_appareils.pack(fill="x", pady=(0, 8))

        colonnes = (
            "nom",
            "mac",
            "rssi",
            "canal",
            "trames",
            "duree_moyenne",
        )

        self.table_appareils = ttk.Treeview(
            cadre_appareils,
            columns=colonnes,
            show="headings",
            height=6,
            selectmode="browse",
        )

        titres = {
            "nom": "Nom",
            "mac": "Adresse MAC",
            "rssi": "Dernier RSSI",
            "canal": "Canal",
            "trames": "Nb trames",
            "duree_moyenne": "Durée moyenne",
        }

        largeurs = {
            "nom": 170,
            "mac": 180,
            "rssi": 100,
            "canal": 80,
            "trames": 90,
            "duree_moyenne": 130,
        }

        for colonne in colonnes:
            self.table_appareils.heading(
                colonne,
                text=titres[colonne],
            )
            self.table_appareils.column(
                colonne,
                width=largeurs[colonne],
                anchor="center",
            )

        barre = ttk.Scrollbar(
            cadre_appareils,
            orient="vertical",
            command=self.table_appareils.yview,
        )
        self.table_appareils.configure(yscrollcommand=barre.set)

        self.table_appareils.pack(
            side="left",
            fill="x",
            expand=True,
        )
        barre.pack(side="right", fill="y")

        self.table_appareils.bind(
            "<<TreeviewSelect>>",
            self._sur_selection_table,
        )
        self.table_appareils.bind(
            "<Double-1>",
            self._sur_double_clic_table,
        )

        # Dernière trame
        cadre_trame = ttk.LabelFrame(
            droite,
            text="Dernière trame affichée",
            padding=10,
        )
        cadre_trame.pack(fill="x", pady=(0, 8))

        self.vars_trame = {
            "numero": tk.StringVar(value="Trame : ---"),
            "nom": tk.StringVar(value="Appareil : ---"),
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
            ("nom", 0, 1),
            ("mac", 1, 0),
            ("rssi", 1, 1),
            ("canal", 2, 0),
            ("longueur", 2, 1),
            ("duree", 3, 0),
            ("type", 3, 1),
            ("debut", 4, 0),
            ("fin", 4, 1),
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

        # Statistiques
        cadre_stats = ttk.LabelFrame(
            droite,
            text="Statistiques du filtre actif",
            padding=10,
        )
        cadre_stats.pack(fill="x", pady=(0, 8))

        self.vars_stats = {
            "nb": tk.StringVar(value="Nombre de trames : 0"),
            "appareils": tk.StringVar(value="Appareils détectés : 0"),
            "duree_moy": tk.StringVar(value="Durée moyenne : ---"),
            "duree_min": tk.StringVar(value="Durée min : ---"),
            "duree_max": tk.StringVar(value="Durée max : ---"),
            "rssi_moy": tk.StringVar(value="RSSI moyen : ---"),
            "rssi_min": tk.StringVar(value="RSSI min : ---"),
            "rssi_max": tk.StringVar(value="RSSI max : ---"),
            "temps": tk.StringVar(value="Temps acquisition : 0,0 s"),
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
                pady=3,
            )

        for colonne in range(3):
            cadre_stats.columnconfigure(colonne, weight=1)

        # Graphes
        cadre_graphes = ttk.LabelFrame(
            droite,
            text="Graphes en temps réel",
            padding=8,
        )
        cadre_graphes.pack(fill="both", expand=True)

        self.figure, (self.ax_rssi, self.ax_duree) = plt.subplots(
            2,
            1,
            figsize=(10, 7),
        )
        self.figure.subplots_adjust(hspace=0.50)

        self.canvas = FigureCanvasTkAgg(
            self.figure,
            master=cadre_graphes,
        )
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # =========================================================================
    # NOMS BLUETOOTH CONNUS
    # =========================================================================

    @staticmethod
    def _charger_noms_bluetoothctl() -> dict[str, str]:
        """
        Charge les noms connus de bluetoothctl.
        Les adresses aléatoires des annonces BLE peuvent différer
        de l'adresse d'appairage du téléphone.
        """
        noms: dict[str, str] = {}

        try:
            sortie = subprocess.check_output(
                ["bluetoothctl", "devices"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )

            for ligne in sortie.splitlines():
                morceaux = ligne.strip().split(maxsplit=2)

                if len(morceaux) >= 3 and morceaux[0] == "Device":
                    mac = morceaux[1].lower()
                    nom = morceaux[2]
                    noms[mac] = nom

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
            self.temps_demarrage_local = time.time()

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

        self.driver = None
        self.capture_active = False

        self.label_etat.config(text="Capture arrêtée")
        self.bouton_demarrer.config(state="normal")
        self.bouton_arreter.config(state="disabled")

    def fermer(self) -> None:
        self.arreter()
        self.fenetre.destroy()

    def _traiter_file_trames(self) -> None:
        nouvelles = 0

        while True:
            try:
                trame = self.file_trames.get_nowait()
            except queue.Empty:
                break

            self._enregistrer_trame(trame)
            nouvelles += 1

        if nouvelles:
            self._rafraichir_table_appareils()
            self._recalculer_trames_affichees()
            self._mettre_a_jour_graphes()
            self._mettre_a_jour_statistiques()

        if self.capture_active and self.temps_demarrage_local is not None:
            duree = time.time() - self.temps_demarrage_local
            self.vars_stats["temps"].set(
                f"Temps acquisition : {duree:.1f} s"
            )

        self.fenetre.after(PERIODE_MAJ_MS, self._traiter_file_trames)

    def _enregistrer_trame(self, trame: BLEFrame) -> None:
        self.toutes_trames.append(trame)

        mac = trame.mac.lower()

        if mac not in self.appareils:
            self.appareils[mac] = StatistiquesAppareil(
                mac=trame.mac,
                nom=self._nom_appareil(trame.mac),
            )

        self.appareils[mac].ajouter(trame)

        # Si le filtre laisse passer cette trame, actualiser la dernière trame.
        if self._trame_correspond_filtre(trame):
            self._afficher_derniere_trame(trame)

    def _trame_correspond_filtre(self, trame: BLEFrame) -> bool:
        if self.mac_cible is None:
            return True

        return trame.mac.lower() == self.mac_cible.lower()

    # =========================================================================
    # FILTRE APPAREIL
    # =========================================================================

    def _sur_selection_combo(self, _event: object = None) -> None:
        selection = self.var_selection.get()

        if selection == TOUS_APPAREILS:
            self.mac_cible = None
            self.filtre_verrouille = False
            self.label_filtre.config(text="Filtre : tous les appareils")
        else:
            # Format du menu : "Nom | MAC"
            morceaux = selection.rsplit("|", maxsplit=1)

            if len(morceaux) == 2:
                self.mac_cible = morceaux[1].strip().lower()
                self.label_filtre.config(
                    text=f"Filtre sélectionné : {self.mac_cible}"
                )

        self._recalculer_trames_affichees()
        self._mettre_a_jour_graphes()
        self._mettre_a_jour_statistiques()

    def verrouiller_appareil(self) -> None:
        selection = self.var_selection.get()

        if selection == TOUS_APPAREILS:
            messagebox.showwarning(
                "Sélection nécessaire",
                "Sélectionne d'abord un appareil BLE.",
            )
            return

        self._sur_selection_combo()
        self.filtre_verrouille = True

        self.label_filtre.config(
            text=f"Appareil verrouillé : {self.mac_cible}"
        )
        self.bouton_verrouiller.config(
            text="Appareil verrouillé"
        )

    def afficher_tous(self) -> None:
        self.mac_cible = None
        self.filtre_verrouille = False
        self.var_selection.set(TOUS_APPAREILS)
        self.label_filtre.config(text="Filtre : tous les appareils")
        self.bouton_verrouiller.config(
            text="Verrouiller cet appareil"
        )

        self._recalculer_trames_affichees()
        self._mettre_a_jour_graphes()
        self._mettre_a_jour_statistiques()

    def _sur_selection_table(self, _event: object = None) -> None:
        selection = self.table_appareils.selection()

        if not selection:
            return

        iid = selection[0]
        mac = str(self.table_appareils.item(iid, "values")[1]).lower()

        appareil = self.appareils.get(mac)

        if appareil is None:
            return

        affichage = f"{appareil.nom} | {appareil.mac}"
        self.var_selection.set(affichage)

        if not self.filtre_verrouille:
            self.mac_cible = mac
            self.label_filtre.config(
                text=f"Filtre sélectionné : {appareil.mac}"
            )
            self._recalculer_trames_affichees()
            self._mettre_a_jour_graphes()
            self._mettre_a_jour_statistiques()

    def _sur_double_clic_table(self, _event: object = None) -> None:
        self._sur_selection_table()
        self.verrouiller_appareil()

    # =========================================================================
    # TABLEAU DES APPAREILS
    # =========================================================================

    def _rafraichir_table_appareils(self) -> None:
        for iid in self.table_appareils.get_children():
            self.table_appareils.delete(iid)

        appareils_tries = sorted(
            self.appareils.values(),
            key=lambda appareil: appareil.nb_trames,
            reverse=True,
        )

        valeurs_combo = [TOUS_APPAREILS]

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

            valeurs_combo.append(
                f"{appareil.nom} | {appareil.mac}"
            )

        self.combo_appareils.configure(values=valeurs_combo)

        self.vars_stats["appareils"].set(
            f"Appareils détectés : {len(self.appareils)}"
        )

    # =========================================================================
    # AFFICHAGE ET STATISTIQUES
    # =========================================================================

    def _recalculer_trames_affichees(self) -> None:
        if self.mac_cible is None:
            self.trames_affichees = list(self.toutes_trames)
        else:
            cible = self.mac_cible.lower()
            self.trames_affichees = [
                trame
                for trame in self.toutes_trames
                if trame.mac.lower() == cible
            ]

        if self.trames_affichees:
            self._afficher_derniere_trame(
                self.trames_affichees[-1]
            )
        else:
            self._effacer_derniere_trame()

    def _afficher_derniere_trame(self, trame: BLEFrame) -> None:
        nom = self._nom_appareil(trame.mac)

        self.vars_trame["numero"].set(
            f"Trame : {trame.numero}"
        )
        self.vars_trame["nom"].set(
            f"Appareil : {nom}"
        )
        self.vars_trame["mac"].set(
            f"MAC : {trame.mac}"
        )
        self.vars_trame["rssi"].set(
            f"RSSI : "
            f"{trame.rssi_dbm if trame.rssi_dbm is not None else '---'} dBm"
        )
        self.vars_trame["canal"].set(
            f"Canal : "
            f"{trame.canal if trame.canal is not None else '---'}"
        )
        self.vars_trame["longueur"].set(
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

    def _effacer_derniere_trame(self) -> None:
        libelles = {
            "numero": "Trame",
            "nom": "Appareil",
            "mac": "MAC",
            "rssi": "RSSI",
            "canal": "Canal",
            "longueur": "Payload",
            "duree": "Durée",
            "debut": "Début",
            "fin": "Fin",
            "type": "Type PDU",
        }

        for cle, variable in self.vars_trame.items():
            variable.set(f"{libelles[cle]} : ---")

    def _mettre_a_jour_statistiques(self) -> None:
        trames = self.trames_affichees

        self.vars_stats["nb"].set(
            f"Nombre de trames : {len(trames)}"
        )

        if not trames:
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
            return

        durees = [trame.duree_us for trame in trames]
        rssis = [
            trame.rssi_dbm
            for trame in trames
            if trame.rssi_dbm is not None
        ]

        self.vars_stats["duree_moy"].set(
            f"Durée moyenne : {statistics.mean(durees):.1f} µs"
        )
        self.vars_stats["duree_min"].set(
            f"Durée min : {min(durees):.1f} µs"
        )
        self.vars_stats["duree_max"].set(
            f"Durée max : {max(durees):.1f} µs"
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

    # =========================================================================
    # GRAPHES
    # =========================================================================

    def _configurer_graphes(self) -> None:
        self.ax_rssi.set_title("RSSI des trames BLE")
        self.ax_rssi.set_xlabel("Temps relatif (s)")
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)

        self.ax_duree.set_title("Durée radio des trames BLE")
        self.ax_duree.set_xlabel("Temps relatif (s)")
        self.ax_duree.set_ylabel("Durée (µs)")
        self.ax_duree.grid(True)

        self.canvas.draw_idle()

    def _mettre_a_jour_graphes(self) -> None:
        self.ax_rssi.clear()
        self.ax_duree.clear()

        trames = self.trames_affichees[-MAX_POINTS_AFFICHES:]

        if trames:
            origine = trames[0].debut_s
            temps = [
                trame.debut_s - origine
                for trame in trames
            ]

            temps_rssi: list[float] = []
            valeurs_rssi: list[int] = []

            for temps_relatif, trame in zip(temps, trames):
                if trame.rssi_dbm is not None:
                    temps_rssi.append(temps_relatif)
                    valeurs_rssi.append(trame.rssi_dbm)

            durees = [trame.duree_us for trame in trames]

            if valeurs_rssi:
                self.ax_rssi.plot(
                    temps_rssi,
                    valeurs_rssi,
                    marker=".",
                    linewidth=1,
                )

            self.ax_duree.plot(
                temps,
                durees,
                marker=".",
                linewidth=1,
            )

        self._configurer_graphes()

    # =========================================================================
    # SAUVEGARDES
    # =========================================================================

    def exporter_csv_filtre(self) -> None:
        if not self.trames_affichees:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune trame ne correspond au filtre actif.",
            )
            return

        with CSV_FILTRE.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as fichier:
            writer = csv.writer(fichier)

            writer.writerow([
                "numero_trame",
                "nom_appareil",
                "mac",
                "temps_debut_s",
                "temps_fin_s",
                "duree_us",
                "rssi_dbm",
                "canal",
                "longueur_payload_octets",
                "type_pdu",
                "phy",
            ])

            for trame in self.trames_affichees:
                writer.writerow([
                    trame.numero,
                    self._nom_appareil(trame.mac),
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
            text=f"CSV filtré sauvegardé : {CSV_FILTRE}"
        )

    def sauvegarder_png(self) -> None:
        if not self.trames_affichees:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune trame ne correspond au filtre actif.",
            )
            return

        self.figure.savefig(
            PNG_GRAPHES,
            dpi=300,
            bbox_inches="tight",
        )

        self.label_etat.config(
            text=f"Graphes sauvegardés : {PNG_GRAPHES}"
        )

    def generer_rapport(self) -> None:
        trames = self.trames_affichees

        if not trames:
            messagebox.showwarning(
                "Aucune donnée",
                "Aucune trame ne correspond au filtre actif.",
            )
            return

        durees = [trame.duree_us for trame in trames]
        rssis = [
            trame.rssi_dbm
            for trame in trames
            if trame.rssi_dbm is not None
        ]

        compte_canaux = defaultdict(int)

        for trame in trames:
            compte_canaux[trame.canal] += 1

        filtre = (
            "Tous les appareils"
            if self.mac_cible is None
            else self.mac_cible
        )

        with RAPPORT_TXT.open(
            "w",
            encoding="utf-8",
        ) as rapport:
            rapport.write("RAPPORT D'ACQUISITION BLE V4\n")
            rapport.write("============================\n\n")
            rapport.write(f"Date : {datetime.now()}\n")
            rapport.write(
                "Matériel : Adafruit Bluefruit LE Sniffer nRF51822\n"
            )
            rapport.write("PHY : LE 1M\n")
            rapport.write(f"Filtre : {filtre}\n")
            rapport.write(
                f"Nombre d'appareils détectés : {len(self.appareils)}\n"
            )
            rapport.write(
                f"Nombre de trames analysées : {len(trames)}\n\n"
            )

            rapport.write("DURÉE DES TRAMES\n")
            rapport.write("-----------------\n")
            rapport.write(
                f"Durée moyenne : {statistics.mean(durees):.3f} µs\n"
            )
            rapport.write(
                f"Durée minimale : {min(durees):.3f} µs\n"
            )
            rapport.write(
                f"Durée maximale : {max(durees):.3f} µs\n\n"
            )

            if rssis:
                rapport.write("RSSI\n")
                rapport.write("----\n")
                rapport.write(
                    f"RSSI moyen : {statistics.mean(rssis):.2f} dBm\n"
                )
                rapport.write(
                    f"RSSI minimal : {min(rssis)} dBm\n"
                )
                rapport.write(
                    f"RSSI maximal : {max(rssis)} dBm\n\n"
                )

            rapport.write("RÉPARTITION PAR CANAL\n")
            rapport.write("---------------------\n")

            for canal in (37, 38, 39):
                rapport.write(
                    f"Canal {canal} : {compte_canaux[canal]} trames\n"
                )

            rapport.write("\nAPPAREILS DÉTECTÉS\n")
            rapport.write("-------------------\n")

            for appareil in sorted(
                self.appareils.values(),
                key=lambda valeur: valeur.nb_trames,
                reverse=True,
            ):
                rapport.write(
                    f"{appareil.nom} | {appareil.mac} | "
                    f"{appareil.nb_trames} trames | "
                    f"RSSI moyen="
                    f"{appareil.rssi_moyen if appareil.rssi_moyen is not None else '---'} | "
                    f"Durée moyenne="
                    f"{appareil.duree_moyenne if appareil.duree_moyenne is not None else '---'} µs\n"
                )

        self.label_etat.config(
            text=f"Rapport généré : {RAPPORT_TXT}"
        )

    # =========================================================================
    # RÉINITIALISATION
    # =========================================================================

    def reinitialiser_affichage(self) -> None:
        self.toutes_trames.clear()
        self.trames_affichees.clear()
        self.appareils.clear()
        self.mac_cible = None
        self.filtre_verrouille = False

        self.var_selection.set(TOUS_APPAREILS)
        self.combo_appareils.configure(
            values=[TOUS_APPAREILS]
        )
        self.label_filtre.config(
            text="Filtre : tous les appareils"
        )
        self.bouton_verrouiller.config(
            text="Verrouiller cet appareil"
        )

        for iid in self.table_appareils.get_children():
            self.table_appareils.delete(iid)

        self._effacer_derniere_trame()

        self.vars_stats["nb"].set(
            "Nombre de trames : 0"
        )
        self.vars_stats["appareils"].set(
            "Appareils détectés : 0"
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
        self.vars_stats["temps"].set(
            "Temps acquisition : 0,0 s"
        )

        self.ax_rssi.clear()
        self.ax_duree.clear()
        self._configurer_graphes()

        self.label_etat.config(
            text="Affichage réinitialisé"
        )


# =============================================================================
# LANCEMENT
# =============================================================================

def main() -> None:
    fenetre = tk.Tk()
    InterfaceBLEV4(fenetre)
    fenetre.mainloop()


if __name__ == "__main__":
    main()
