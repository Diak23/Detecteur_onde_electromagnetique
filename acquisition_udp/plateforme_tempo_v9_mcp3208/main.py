#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import random
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Optional
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


APP_TITLE = "Plateforme TEMPO V9 — acquisition analogique MCP3208"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "acquisitions_tempo_v9"

ADC_MAX = 4095
DEFAULT_VREF = 3.3
DEFAULT_SAMPLE_PERIOD_MS = 100

ALERT_COLORS = {
    "VERT": "#2e9d50",
    "ORANGE": "#ef8f00",
    "ROUGE": "#c62828",
}


@dataclass
class BandConfig:
    name: str
    frequency_mhz: float
    adc_channel: int
    detector_reference_voltage_v: float
    detector_reference_power_dbm: float
    detector_slope_v_per_db: float
    lna_gain_db: float
    filter_loss_db: float
    cable_loss_db: float
    switch_loss_db: float

    @property
    def net_gain_db(self) -> float:
        return (
            self.lna_gain_db
            - self.filter_loss_db
            - self.cable_loss_db
            - self.switch_loss_db
        )


@dataclass
class Measurement:
    timestamp_epoch: float
    elapsed_s: float
    band: str
    frequency_mhz: float
    adc_channel: int
    adc_code: int
    voltage_v: float
    detector_power_dbm: float
    antenna_power_dbm: float
    antenna_power_w: float
    sample_duration_s: float
    energy_j: float
    cumulative_energy_j: float
    alert_level: str
    net_gain_db: float
    mode: str

    def as_dict(self):
        return asdict(self)


class MCP3208Reader:
    def __init__(self, bus=0, device=0, max_speed_hz=1_000_000):
        self.bus = bus
        self.device = device
        self.max_speed_hz = max_speed_hz
        self.spi = None

    def open(self):
        try:
            import spidev
        except ImportError as exc:
            raise RuntimeError(
                "Le module spidev n'est pas installé. Exécutez ./install.sh"
            ) from exc

        self.spi = spidev.SpiDev()
        self.spi.open(self.bus, self.device)
        self.spi.max_speed_hz = self.max_speed_hz
        self.spi.mode = 0b00

    def read_channel(self, channel: int) -> int:
        if self.spi is None:
            raise RuntimeError("SPI non initialisé.")
        if channel < 0 or channel > 7:
            raise ValueError("Le canal MCP3208 doit être compris entre 0 et 7.")

        # Start bit + single-ended + D2
        command_1 = 0b00000110 | ((channel & 0b100) >> 2)
        command_2 = (channel & 0b011) << 6
        response = self.spi.xfer2([command_1, command_2, 0x00])
        return ((response[1] & 0x0F) << 8) | response[2]

    def close(self):
        if self.spi is not None:
            self.spi.close()
            self.spi = None


def adc_to_voltage(adc_code: int, vref: float) -> float:
    return adc_code * vref / ADC_MAX


def detector_voltage_to_power_dbm(
    voltage_v: float,
    reference_voltage_v: float,
    reference_power_dbm: float,
    slope_v_per_db: float,
) -> float:
    """
    Modèle linéaire calibrable :
    P = P_ref + (V - V_ref) / pente

    Le ZX47-40-S+ a une pente négative typique, proche de -25 mV/dB.
    """
    if abs(slope_v_per_db) < 1e-12:
        raise ValueError("La pente du détecteur ne peut pas être nulle.")

    return reference_power_dbm + (
        voltage_v - reference_voltage_v
    ) / slope_v_per_db


def dbm_to_watts(power_dbm: float) -> float:
    return 10 ** ((power_dbm - 30.0) / 10.0)


def classify_alert(power_dbm: float, orange_threshold: float, red_threshold: float):
    if orange_threshold >= red_threshold:
        raise ValueError("Le seuil orange doit être inférieur au seuil rouge.")
    if power_dbm >= red_threshold:
        return "ROUGE"
    if power_dbm >= orange_threshold:
        return "ORANGE"
    return "VERT"


class TempoHardwareApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1550x950")
        self.minsize(1250, 780)

        self.reader: Optional[MCP3208Reader] = None
        self.running = False
        self.worker_thread = None
        self.stop_event = threading.Event()

        self.measurements = []
        self.cumulative_energy = defaultdict(float)
        self.acquisition_started_monotonic = None
        self.acquisition_started_datetime = None
        self.stop_reason = ""

        self.simulation_var = tk.BooleanVar(value=True)
        self.spi_bus_var = tk.StringVar(value="0")
        self.spi_device_var = tk.StringVar(value="0")
        self.vref_var = tk.StringVar(value=str(DEFAULT_VREF))
        self.sample_period_var = tk.StringVar(value=str(DEFAULT_SAMPLE_PERIOD_MS))

        self.acquisition_mode_var = tk.StringVar(value="Acquisition complète")
        self.duration_choice_var = tk.StringVar(value="30 secondes")
        self.custom_duration_var = tk.StringVar(value="30")
        self.sample_limit_var = tk.StringVar(value="1000")

        self.orange_threshold_var = tk.StringVar(value="-70")
        self.red_threshold_var = tk.StringVar(value="-50")

        self.status_var = tk.StringVar(value="Capture arrêtée")
        self.progress_var = tk.StringVar(value="Prêt")
        self.global_alert_var = tk.StringVar(value="VERT")
        self.total_energy_var = tk.StringVar(value="0 J")
        self.elapsed_var = tk.StringVar(value="0.0 s")

        self.graph_var = tk.StringVar(value="Puissance à l'antenne")

        self.band_vars = {
            "868 MHz": self._make_band_vars(
                frequency=868.0,
                channel=0,
                v_ref=2.10,
                p_ref=-40.0,
                slope=-0.025,
                gain=35.0,
                filter_loss=1.0,
                cable_loss=1.0,
                switch_loss=0.0,
            ),
            "2,45 GHz": self._make_band_vars(
                frequency=2450.0,
                channel=1,
                v_ref=2.10,
                p_ref=-40.0,
                slope=-0.025,
                gain=18.5,
                filter_loss=0.8,
                cable_loss=1.0,
                switch_loss=0.0,
            ),
        }

        self._build_interface()
        self.after(200, self._refresh_dashboard)
        self.protocol("WM_DELETE_WINDOW", self.close_application)

    @staticmethod
    def _make_band_vars(
        frequency,
        channel,
        v_ref,
        p_ref,
        slope,
        gain,
        filter_loss,
        cable_loss,
        switch_loss,
    ):
        return {
            "frequency": tk.StringVar(value=str(frequency)),
            "channel": tk.StringVar(value=str(channel)),
            "v_ref": tk.StringVar(value=str(v_ref)),
            "p_ref": tk.StringVar(value=str(p_ref)),
            "slope": tk.StringVar(value=str(slope)),
            "gain": tk.StringVar(value=str(gain)),
            "filter_loss": tk.StringVar(value=str(filter_loss)),
            "cable_loss": tk.StringVar(value=str(cable_loss)),
            "switch_loss": tk.StringVar(value=str(switch_loss)),
        }

    def _build_interface(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=10)

        ttk.Label(
            header,
            text=APP_TITLE,
            font=("TkDefaultFont", 16, "bold"),
        ).pack(side="left")

        ttk.Label(
            header,
            textvariable=self.status_var,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=8)

        self.dashboard_tab = ttk.Frame(notebook)
        self.config_tab = ttk.Frame(notebook)
        self.calibration_tab = ttk.Frame(notebook)
        self.measurements_tab = ttk.Frame(notebook)
        self.graphs_tab = ttk.Frame(notebook)
        self.journal_tab = ttk.Frame(notebook)

        notebook.add(self.dashboard_tab, text="Tableau de bord")
        notebook.add(self.config_tab, text="Configuration acquisition")
        notebook.add(self.calibration_tab, text="Calibration / bilan de puissance")
        notebook.add(self.measurements_tab, text="Mesures")
        notebook.add(self.graphs_tab, text="Graphiques")
        notebook.add(self.journal_tab, text="Journal")

        self._build_dashboard()
        self._build_config()
        self._build_calibration()
        self._build_measurements()
        self._build_graphs()
        self._build_journal()

    def _build_dashboard(self):
        self.alert_banner = tk.Label(
            self.dashboard_tab,
            textvariable=self.global_alert_var,
            bg=ALERT_COLORS["VERT"],
            fg="white",
            font=("TkDefaultFont", 28, "bold"),
            relief="raised",
            padx=20,
            pady=18,
        )
        self.alert_banner.pack(fill="x", padx=12, pady=12)

        summary = ttk.Frame(self.dashboard_tab)
        summary.pack(fill="x", padx=12, pady=8)

        for index, (title, variable) in enumerate([
            ("Durée d'acquisition", self.elapsed_var),
            ("Énergie RF reçue estimée", self.total_energy_var),
            ("État", self.status_var),
        ]):
            card = ttk.LabelFrame(summary, text=title)
            card.grid(row=0, column=index, sticky="nsew", padx=6)
            ttk.Label(
                card,
                textvariable=variable,
                font=("TkDefaultFont", 15, "bold"),
            ).pack(padx=25, pady=18)
            summary.columnconfigure(index, weight=1)

        columns = (
            "band",
            "frequency",
            "adc",
            "voltage",
            "p_detector",
            "p_antenna",
            "power_w",
            "energy",
            "alert",
        )

        self.dashboard_table = ttk.Treeview(
            self.dashboard_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "band": "Bande",
            "frequency": "Fréquence (MHz)",
            "adc": "Code ADC",
            "voltage": "Tension (V)",
            "p_detector": "P détecteur (dBm)",
            "p_antenna": "P antenne (dBm)",
            "power_w": "P antenne (W)",
            "energy": "Énergie cumulée (J)",
            "alert": "Alerte",
        }

        for column in columns:
            self.dashboard_table.heading(column, text=headings[column])
            self.dashboard_table.column(column, width=150, anchor="center")

        self.dashboard_table.tag_configure(
            "VERT", background="#c8f2d3", foreground="#10451f"
        )
        self.dashboard_table.tag_configure(
            "ORANGE", background="#ffe0a3", foreground="#663c00"
        )
        self.dashboard_table.tag_configure(
            "ROUGE", background="#f6b2b2", foreground="#681414"
        )

        self.dashboard_table.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_config(self):
        frame = ttk.LabelFrame(self.config_tab, text="Interface MCP3208")
        frame.pack(fill="x", padx=12, pady=12)
        frame.columnconfigure(1, weight=1)

        rows = [
            ("Mode simulation", self.simulation_var),
            ("Bus SPI", self.spi_bus_var),
            ("Périphérique SPI", self.spi_device_var),
            ("Tension de référence ADC (V)", self.vref_var),
            ("Période d'échantillonnage (ms)", self.sample_period_var),
        ]

        for row, (label, variable) in enumerate(rows):
            ttk.Label(frame, text=label).grid(
                row=row, column=0, padx=8, pady=6, sticky="w"
            )

            if isinstance(variable, tk.BooleanVar):
                widget = ttk.Checkbutton(frame, variable=variable)
            else:
                widget = ttk.Entry(frame, textvariable=variable)

            widget.grid(row=row, column=1, padx=8, pady=6, sticky="ew")

        acquisition = ttk.LabelFrame(
            self.config_tab,
            text="Mode d'acquisition",
        )
        acquisition.pack(fill="x", padx=12, pady=8)
        acquisition.columnconfigure(1, weight=1)

        ttk.Label(acquisition, text="Mode").grid(
            row=0, column=0, padx=8, pady=6
        )
        ttk.Combobox(
            acquisition,
            textvariable=self.acquisition_mode_var,
            values=(
                "Acquisition complète",
                "Durée limitée",
                "Nombre d'échantillons",
            ),
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(acquisition, text="Durée prédéfinie").grid(
            row=1, column=0, padx=8, pady=6
        )
        ttk.Combobox(
            acquisition,
            textvariable=self.duration_choice_var,
            values=(
                "30 secondes",
                "1 minute",
                "2 minutes",
                "5 minutes",
                "Durée personnalisée",
            ),
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(acquisition, text="Durée personnalisée (s)").grid(
            row=2, column=0, padx=8, pady=6
        )
        ttk.Entry(
            acquisition,
            textvariable=self.custom_duration_var,
        ).grid(row=2, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(acquisition, text="Limite d'échantillons").grid(
            row=3, column=0, padx=8, pady=6
        )
        ttk.Entry(
            acquisition,
            textvariable=self.sample_limit_var,
        ).grid(row=3, column=1, sticky="ew", padx=8, pady=6)

        alerts = ttk.LabelFrame(self.config_tab, text="Seuils d'alerte")
        alerts.pack(fill="x", padx=12, pady=8)
        alerts.columnconfigure(1, weight=1)

        ttk.Label(alerts, text="Vert → Orange (dBm antenne)").grid(
            row=0, column=0, padx=8, pady=6
        )
        ttk.Entry(
            alerts,
            textvariable=self.orange_threshold_var,
        ).grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(alerts, text="Orange → Rouge (dBm antenne)").grid(
            row=1, column=0, padx=8, pady=6
        )
        ttk.Entry(
            alerts,
            textvariable=self.red_threshold_var,
        ).grid(row=1, column=1, sticky="ew", padx=8, pady=6)

        buttons = ttk.Frame(self.config_tab)
        buttons.pack(fill="x", padx=12, pady=14)

        ttk.Button(
            buttons,
            text="Démarrer",
            command=self.start_acquisition,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Arrêter et sauvegarder",
            command=lambda: self.stop_acquisition("arrêt manuel"),
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Effacer",
            command=self.clear_data,
        ).pack(side="left", padx=5)

        self.progress_bar = ttk.Progressbar(
            self.config_tab,
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.pack(fill="x", padx=12, pady=8)

        ttk.Label(
            self.config_tab,
            textvariable=self.progress_var,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", padx=12, pady=6)

    def _build_calibration(self):
        note = (
            "Les valeurs ci-dessous proviennent du bilan de puissance préliminaire. "
            "Elles doivent être remplacées par les résultats de calibration mesurés."
        )
        ttk.Label(
            self.calibration_tab,
            text=note,
            wraplength=1250,
            font=("TkDefaultFont", 10, "italic"),
        ).pack(anchor="w", padx=12, pady=10)

        for band_name, variables in self.band_vars.items():
            frame = ttk.LabelFrame(
                self.calibration_tab,
                text=f"Chaîne {band_name}",
            )
            frame.pack(fill="x", padx=12, pady=8)
            frame.columnconfigure(1, weight=1)
            frame.columnconfigure(3, weight=1)

            fields = [
                ("Fréquence (MHz)", "frequency"),
                ("Canal MCP3208", "channel"),
                ("Vref détecteur (V)", "v_ref"),
                ("Pref détecteur (dBm)", "p_ref"),
                ("Pente détecteur (V/dB)", "slope"),
                ("Gain LNA (dB)", "gain"),
                ("Perte filtre (dB)", "filter_loss"),
                ("Perte câbles (dB)", "cable_loss"),
                ("Perte commutateur (dB)", "switch_loss"),
            ]

            for index, (label, key) in enumerate(fields):
                row = index // 2
                col = (index % 2) * 2
                ttk.Label(frame, text=label).grid(
                    row=row, column=col, padx=8, pady=5, sticky="w"
                )
                ttk.Entry(
                    frame,
                    textvariable=variables[key],
                ).grid(
                    row=row,
                    column=col + 1,
                    padx=8,
                    pady=5,
                    sticky="ew",
                )

        ttk.Button(
            self.calibration_tab,
            text="Vérifier le bilan de puissance",
            command=self.show_power_budget,
        ).pack(pady=12)

        self.power_budget_text = tk.Text(
            self.calibration_tab,
            height=13,
        )
        self.power_budget_text.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=10,
        )

    def _build_measurements(self):
        columns = (
            "time",
            "elapsed",
            "band",
            "adc",
            "voltage",
            "p_detector",
            "p_antenna",
            "power",
            "energy",
            "alert",
        )

        self.measurement_table = ttk.Treeview(
            self.measurements_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "time": "Heure",
            "elapsed": "Temps (s)",
            "band": "Bande",
            "adc": "ADC",
            "voltage": "Tension (V)",
            "p_detector": "P détecteur (dBm)",
            "p_antenna": "P antenne (dBm)",
            "power": "Puissance (W)",
            "energy": "Énergie (J)",
            "alert": "Alerte",
        }

        for column in columns:
            self.measurement_table.heading(column, text=headings[column])
            self.measurement_table.column(column, width=145, anchor="center")

        self.measurement_table.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_graphs(self):
        controls = ttk.Frame(self.graphs_tab)
        controls.pack(fill="x", padx=12, pady=8)

        ttk.Combobox(
            controls,
            textvariable=self.graph_var,
            values=(
                "Puissance à l'antenne",
                "Tension du détecteur",
                "Code ADC",
                "Énergie cumulée",
            ),
            state="readonly",
            width=30,
        ).pack(side="left", padx=8)

        ttk.Button(
            controls,
            text="Actualiser",
            command=self.draw_graph,
        ).pack(side="left")

        self.figure = Figure(figsize=(12, 7), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(
            self.figure,
            master=self.graphs_tab,
        )
        self.canvas.get_tk_widget().pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_journal(self):
        self.log_text = tk.Text(self.journal_tab)
        self.log_text.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def parse_band_configs(self):
        configs = []

        for name, variables in self.band_vars.items():
            config = BandConfig(
                name=name,
                frequency_mhz=float(
                    variables["frequency"].get().replace(",", ".")
                ),
                adc_channel=int(variables["channel"].get()),
                detector_reference_voltage_v=float(
                    variables["v_ref"].get().replace(",", ".")
                ),
                detector_reference_power_dbm=float(
                    variables["p_ref"].get().replace(",", ".")
                ),
                detector_slope_v_per_db=float(
                    variables["slope"].get().replace(",", ".")
                ),
                lna_gain_db=float(
                    variables["gain"].get().replace(",", ".")
                ),
                filter_loss_db=float(
                    variables["filter_loss"].get().replace(",", ".")
                ),
                cable_loss_db=float(
                    variables["cable_loss"].get().replace(",", ".")
                ),
                switch_loss_db=float(
                    variables["switch_loss"].get().replace(",", ".")
                ),
            )

            if not 0 <= config.adc_channel <= 7:
                raise ValueError(
                    f"Canal invalide pour {name}: {config.adc_channel}"
                )

            configs.append(config)

        return configs

    def selected_duration_seconds(self):
        mapping = {
            "30 secondes": 30.0,
            "1 minute": 60.0,
            "2 minutes": 120.0,
            "5 minutes": 300.0,
        }
        choice = self.duration_choice_var.get()
        if choice in mapping:
            return mapping[choice]
        value = float(self.custom_duration_var.get().replace(",", "."))
        if value <= 0:
            raise ValueError("La durée doit être positive.")
        return value

    def start_acquisition(self):
        if self.running:
            messagebox.showwarning(
                "Acquisition",
                "Une acquisition est déjà active.",
            )
            return

        try:
            self.parse_band_configs()
            vref = float(self.vref_var.get().replace(",", "."))
            period_ms = float(
                self.sample_period_var.get().replace(",", ".")
            )
            orange = float(
                self.orange_threshold_var.get().replace(",", ".")
            )
            red = float(
                self.red_threshold_var.get().replace(",", ".")
            )

            if vref <= 0:
                raise ValueError("La tension de référence doit être positive.")
            if period_ms <= 0:
                raise ValueError(
                    "La période d'échantillonnage doit être positive."
                )
            if orange >= red:
                raise ValueError(
                    "Le seuil orange doit être inférieur au seuil rouge."
                )

            if self.acquisition_mode_var.get() == "Durée limitée":
                self.selected_duration_seconds()
            elif self.acquisition_mode_var.get() == "Nombre d'échantillons":
                if int(self.sample_limit_var.get()) <= 0:
                    raise ValueError(
                        "La limite d'échantillons doit être positive."
                    )

            if not self.simulation_var.get():
                self.reader = MCP3208Reader(
                    bus=int(self.spi_bus_var.get()),
                    device=int(self.spi_device_var.get()),
                )
                self.reader.open()
                self.log("Interface SPI MCP3208 ouverte.")
            else:
                self.reader = None
                self.log("Mode simulation activé.")

            self.running = True
            self.stop_event.clear()
            self.stop_reason = ""
            self.acquisition_started_monotonic = time.monotonic()
            self.acquisition_started_datetime = datetime.now()
            self.status_var.set("Acquisition active")

            self.worker_thread = threading.Thread(
                target=self._acquisition_loop,
                daemon=True,
            )
            self.worker_thread.start()
            self.log("Acquisition démarrée.")

        except Exception as exc:
            if self.reader is not None:
                self.reader.close()
                self.reader = None
            messagebox.showerror("Erreur", str(exc))

    def _simulate_adc(self, config: BandConfig, elapsed: float, vref: float):
        if config.name == "868 MHz":
            power_antenna = -72 + 7 * math.sin(elapsed / 6) + random.gauss(0, 1.5)
        else:
            power_antenna = -66 + 5 * math.sin(elapsed / 8) + random.gauss(0, 1.2)

        detector_power = power_antenna + config.net_gain_db
        voltage = config.detector_reference_voltage_v + (
            detector_power - config.detector_reference_power_dbm
        ) * config.detector_slope_v_per_db

        voltage = max(0.0, min(vref, voltage))
        adc = int(round(voltage / vref * ADC_MAX))
        return max(0, min(ADC_MAX, adc))

    def _acquisition_loop(self):
        try:
            configs = self.parse_band_configs()
            vref = float(self.vref_var.get().replace(",", "."))
            period_s = float(
                self.sample_period_var.get().replace(",", ".")
            ) / 1000.0

            while not self.stop_event.is_set():
                loop_start = time.monotonic()
                elapsed = (
                    loop_start - self.acquisition_started_monotonic
                )

                for config in configs:
                    if self.simulation_var.get():
                        adc_code = self._simulate_adc(
                            config,
                            elapsed,
                            vref,
                        )
                        mode = "simulation"
                    else:
                        adc_code = self.reader.read_channel(
                            config.adc_channel
                        )
                        mode = "MCP3208"

                    voltage = adc_to_voltage(adc_code, vref)
                    detector_power = detector_voltage_to_power_dbm(
                        voltage,
                        config.detector_reference_voltage_v,
                        config.detector_reference_power_dbm,
                        config.detector_slope_v_per_db,
                    )
                    antenna_power = detector_power - config.net_gain_db
                    power_w = dbm_to_watts(antenna_power)
                    energy_j = power_w * period_s
                    self.cumulative_energy[config.name] += energy_j

                    alert = classify_alert(
                        antenna_power,
                        float(
                            self.orange_threshold_var.get().replace(",", ".")
                        ),
                        float(
                            self.red_threshold_var.get().replace(",", ".")
                        ),
                    )

                    measurement = Measurement(
                        timestamp_epoch=time.time(),
                        elapsed_s=elapsed,
                        band=config.name,
                        frequency_mhz=config.frequency_mhz,
                        adc_channel=config.adc_channel,
                        adc_code=adc_code,
                        voltage_v=voltage,
                        detector_power_dbm=detector_power,
                        antenna_power_dbm=antenna_power,
                        antenna_power_w=power_w,
                        sample_duration_s=period_s,
                        energy_j=energy_j,
                        cumulative_energy_j=self.cumulative_energy[
                            config.name
                        ],
                        alert_level=alert,
                        net_gain_db=config.net_gain_db,
                        mode=mode,
                    )
                    self.measurements.append(measurement)
                    self.after(
                        0,
                        lambda m=measurement: self._display_measurement(m),
                    )

                if self._should_auto_stop(elapsed):
                    self.after(
                        0,
                        lambda: self.stop_acquisition(
                            "condition d'arrêt atteinte"
                        ),
                    )
                    break

                sleep_time = period_s - (
                    time.monotonic() - loop_start
                )
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as exc:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Erreur acquisition",
                    str(exc),
                ),
            )
            self.after(
                0,
                lambda: self.stop_acquisition("erreur acquisition"),
            )

    def _should_auto_stop(self, elapsed):
        mode = self.acquisition_mode_var.get()

        if mode == "Durée limitée":
            return elapsed >= self.selected_duration_seconds()

        if mode == "Nombre d'échantillons":
            return len(self.measurements) >= int(
                self.sample_limit_var.get()
            )

        return False

    def _display_measurement(self, measurement):
        self.measurement_table.insert(
            "",
            "end",
            values=(
                datetime.fromtimestamp(
                    measurement.timestamp_epoch
                ).strftime("%H:%M:%S.%f")[:-3],
                f"{measurement.elapsed_s:.3f}",
                measurement.band,
                measurement.adc_code,
                f"{measurement.voltage_v:.6f}",
                f"{measurement.detector_power_dbm:.3f}",
                f"{measurement.antenna_power_dbm:.3f}",
                f"{measurement.antenna_power_w:.6e}",
                f"{measurement.energy_j:.6e}",
                measurement.alert_level,
            ),
        )

        if len(self.measurement_table.get_children()) > 3000:
            first = self.measurement_table.get_children()[0]
            self.measurement_table.delete(first)

    def stop_acquisition(self, reason):
        if not self.running:
            return

        self.running = False
        self.stop_reason = reason
        self.stop_event.set()

        if self.reader is not None:
            try:
                self.reader.close()
            finally:
                self.reader = None

        self.status_var.set("Acquisition arrêtée")
        self.log(f"Acquisition arrêtée : {reason}")

        if self.measurements:
            self.export_results()

    def clear_data(self):
        if self.running:
            messagebox.showwarning(
                "Acquisition",
                "Arrêtez d'abord l'acquisition.",
            )
            return

        self.measurements.clear()
        self.cumulative_energy.clear()
        self.measurement_table.delete(
            *self.measurement_table.get_children()
        )
        self.dashboard_table.delete(
            *self.dashboard_table.get_children()
        )
        self.progress_bar["value"] = 0
        self.progress_var.set("Prêt")
        self.total_energy_var.set("0 J")
        self.elapsed_var.set("0.0 s")
        self.global_alert_var.set("VERT")
        self.alert_banner.configure(bg=ALERT_COLORS["VERT"])
        self.axis.clear()
        self.canvas.draw_idle()

    def show_power_budget(self):
        try:
            configs = self.parse_band_configs()
        except Exception as exc:
            messagebox.showerror("Bilan de puissance", str(exc))
            return

        self.power_budget_text.delete("1.0", "end")

        for config in configs:
            lines = [
                f"=== {config.name} ===",
                f"Fréquence : {config.frequency_mhz:.1f} MHz",
                f"Gain LNA : +{config.lna_gain_db:.2f} dB",
                f"Perte filtre : -{config.filter_loss_db:.2f} dB",
                f"Perte câbles : -{config.cable_loss_db:.2f} dB",
                f"Perte commutateur : -{config.switch_loss_db:.2f} dB",
                f"Gain net : {config.net_gain_db:+.2f} dB",
                "",
                "Relation :",
                "P_detecteur = P_antenne + gain_net",
                "P_antenne = P_detecteur - gain_net",
                "",
            ]
            self.power_budget_text.insert(
                "end",
                "\n".join(lines) + "\n",
            )

    def _refresh_dashboard(self):
        elapsed = (
            time.monotonic() - self.acquisition_started_monotonic
            if self.running and self.acquisition_started_monotonic
            else (
                self.measurements[-1].elapsed_s
                if self.measurements else 0.0
            )
        )
        self.elapsed_var.set(f"{elapsed:.1f} s")

        latest = {}
        for measurement in self.measurements:
            latest[measurement.band] = measurement

        self.dashboard_table.delete(
            *self.dashboard_table.get_children()
        )

        priority = {"VERT": 0, "ORANGE": 1, "ROUGE": 2}
        global_level = "VERT"

        for band in sorted(latest):
            measurement = latest[band]
            if (
                priority[measurement.alert_level]
                > priority[global_level]
            ):
                global_level = measurement.alert_level

            self.dashboard_table.insert(
                "",
                "end",
                values=(
                    measurement.band,
                    f"{measurement.frequency_mhz:.1f}",
                    measurement.adc_code,
                    f"{measurement.voltage_v:.6f}",
                    f"{measurement.detector_power_dbm:.3f}",
                    f"{measurement.antenna_power_dbm:.3f}",
                    f"{measurement.antenna_power_w:.6e}",
                    f"{measurement.cumulative_energy_j:.6e}",
                    measurement.alert_level,
                ),
                tags=(measurement.alert_level,),
            )

        self.global_alert_var.set(global_level)
        self.alert_banner.configure(
            bg=ALERT_COLORS[global_level]
        )
        total_energy = sum(self.cumulative_energy.values())
        self.total_energy_var.set(f"{total_energy:.6e} J")

        if self.running:
            mode = self.acquisition_mode_var.get()
            if mode == "Durée limitée":
                target = self.selected_duration_seconds()
                percent = min(100.0, elapsed / target * 100.0)
                self.progress_bar["value"] = percent
                self.progress_var.set(
                    f"{elapsed:.1f}/{target:.1f} s — {percent:.1f} %"
                )
            elif mode == "Nombre d'échantillons":
                target = int(self.sample_limit_var.get())
                percent = min(
                    100.0,
                    len(self.measurements) / target * 100.0,
                )
                self.progress_bar["value"] = percent
                self.progress_var.set(
                    f"{len(self.measurements)}/{target} mesures — "
                    f"{percent:.1f} %"
                )
            else:
                self.progress_bar["value"] = 0
                self.progress_var.set(
                    f"Acquisition complète — {elapsed:.1f} s — "
                    f"{len(self.measurements)} mesures"
                )

        self.after(200, self._refresh_dashboard)

    def draw_graph(self):
        self.axis.clear()

        grouped = defaultdict(list)
        for measurement in self.measurements:
            grouped[measurement.band].append(measurement)

        graph = self.graph_var.get()

        for band in sorted(grouped):
            values = grouped[band]

            if graph == "Puissance à l'antenne":
                y = [m.antenna_power_dbm for m in values]
                ylabel = "Puissance à l'antenne (dBm)"
            elif graph == "Tension du détecteur":
                y = [m.voltage_v for m in values]
                ylabel = "Tension détecteur (V)"
            elif graph == "Code ADC":
                y = [m.adc_code for m in values]
                ylabel = "Code ADC"
            else:
                y = [m.cumulative_energy_j for m in values]
                ylabel = "Énergie cumulée (J)"

            self.axis.plot(
                [m.elapsed_s for m in values],
                y,
                linewidth=1,
                label=band,
            )

        self.axis.set_title(graph)
        self.axis.set_xlabel("Temps (s)")
        self.axis.set_ylabel(
            ylabel if grouped else "Valeur"
        )
        self.axis.grid(True)

        if grouped:
            self.axis.legend(loc="best")

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def export_results(self):
        folder = OUTPUT_DIR / (
            "acquisition_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        graph_folder = folder / "graphes"
        graph_folder.mkdir(parents=True, exist_ok=True)

        with (folder / "mesures_tempo_mcp3208.csv").open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            rows = [measurement.as_dict() for measurement in self.measurements]
            writer = csv.DictWriter(
                file,
                fieldnames=list(rows[0].keys()),
                delimiter=";",
            )
            writer.writeheader()
            writer.writerows(rows)

        configs = self.parse_band_configs()
        with (folder / "calibration_bilan_puissance.csv").open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "name",
                    "frequency_mhz",
                    "adc_channel",
                    "detector_reference_voltage_v",
                    "detector_reference_power_dbm",
                    "detector_slope_v_per_db",
                    "lna_gain_db",
                    "filter_loss_db",
                    "cable_loss_db",
                    "switch_loss_db",
                    "net_gain_db",
                ],
                delimiter=";",
            )
            writer.writeheader()
            for config in configs:
                row = asdict(config)
                row["net_gain_db"] = config.net_gain_db
                writer.writerow(row)

        with (folder / "synthese.csv").open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["parametre", "valeur"])
            writer.writerow(["mode", self.acquisition_mode_var.get()])
            writer.writerow(["simulation", self.simulation_var.get()])
            writer.writerow(["motif_arret", self.stop_reason])
            writer.writerow(["nombre_mesures", len(self.measurements)])
            writer.writerow([
                "duree_s",
                self.measurements[-1].elapsed_s,
            ])
            writer.writerow([
                "energie_totale_j",
                sum(self.cumulative_energy.values()),
            ])

            for band in sorted({
                measurement.band for measurement in self.measurements
            }):
                values = [
                    measurement
                    for measurement in self.measurements
                    if measurement.band == band
                ]
                writer.writerow([
                    f"{band}_puissance_moyenne_dbm",
                    mean(m.antenna_power_dbm for m in values),
                ])
                writer.writerow([
                    f"{band}_energie_j",
                    values[-1].cumulative_energy_j,
                ])

        graphs = [
            ("Puissance à l'antenne", "puissance_antenne.png"),
            ("Tension du détecteur", "tension_detecteur.png"),
            ("Code ADC", "code_adc.png"),
            ("Énergie cumulée", "energie_cumulee.png"),
        ]
        original = self.graph_var.get()

        for graph_name, filename in graphs:
            self.graph_var.set(graph_name)
            self.draw_graph()
            self.figure.savefig(
                graph_folder / filename,
                dpi=180,
                bbox_inches="tight",
            )

        self.graph_var.set(original)
        self.draw_graph()

        self.log(f"Résultats enregistrés dans : {folder}")
        messagebox.showinfo("Export terminé", str(folder))

    def log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")

    def close_application(self):
        if self.running:
            self.stop_event.set()
        if self.reader is not None:
            try:
                self.reader.close()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    TempoHardwareApp().mainloop()
