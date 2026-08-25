#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import queue
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

from drivers.ble_tshark_driver import BLETsharkCapture, BLEFrame
from drivers.mcp3208_driver import MCP3208Reader
from drivers.lora_driver import LoRaPacket, LoRaSimulationDriver, LoRaSerialJSONDriver, time_on_air_seconds
from drivers.wifi_driver import read_wifi_rssi


APP_TITLE = "Plateforme TEMPO V10.3 — instrument multimode"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "exports"

ADC_MAX = 4095

ALERT_COLORS = {
    "VERT": "#2e9d50",
    "ORANGE": "#ef8f00",
    "ROUGE": "#c62828",
}

# Une acquisition BLE peut contenir de nombreuses adresses publicitaires.
# Toutes les courbes restent tracées, mais la légende est volontairement
# limitée pour ne pas masquer les données.
MAX_BLE_LEGEND_SERIES = 6


@dataclass
class Record:
    timestamp_epoch: float
    elapsed_s: float
    source: str
    technology: str
    band: str
    device: str
    rssi_dbm: Optional[float]
    adc_code: Optional[int]
    voltage_v: Optional[float]
    power_detector_dbm: Optional[float]
    power_antenna_dbm: Optional[float]
    power_w: Optional[float]
    duration_s: float
    energy_j: float
    cumulative_energy_j: float
    packet_length_bytes: Optional[int]
    pdu_type: str
    channel: Optional[int]
    alert_level: str
    simulated: bool

    def as_dict(self):
        return asdict(self)


@dataclass
class RFChain:
    name: str
    band: str
    adc_channel: int
    frequency_mhz: float
    v_ref_detector: float
    p_ref_detector_dbm: float
    slope_v_per_db: float
    lna_gain_db: float
    filter_loss_db: float
    cable_loss_db: float
    switch_loss_db: float

    @property
    def net_gain_db(self):
        return (
            self.lna_gain_db
            - self.filter_loss_db
            - self.cable_loss_db
            - self.switch_loss_db
        )


def dbm_to_watts(dbm: float) -> float:
    return 10 ** ((dbm - 30.0) / 10.0)


def adc_to_voltage(adc_code: int, vref: float) -> float:
    return adc_code * vref / ADC_MAX


def detector_voltage_to_power(
    voltage_v: float,
    v_ref_detector: float,
    p_ref_detector_dbm: float,
    slope_v_per_db: float,
) -> float:
    if abs(slope_v_per_db) < 1e-12:
        raise ValueError("La pente du détecteur ne peut pas être nulle.")
    return p_ref_detector_dbm + (
        voltage_v - v_ref_detector
    ) / slope_v_per_db


def alert_level(power_dbm: Optional[float], orange: float, red: float) -> str:
    if power_dbm is None:
        return "VERT"
    if power_dbm >= red:
        return "ROUGE"
    if power_dbm >= orange:
        return "ORANGE"
    return "VERT"


class TempoV10(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1600x980")
        self.minsize(1280, 800)

        self.records: list[Record] = []
        self.cumulative_energy = defaultdict(float)
        self.running = False
        self.stop_event = threading.Event()
        self.worker_threads = []
        self.event_queue = queue.Queue()

        self.mcp_reader: Optional[MCP3208Reader] = None
        self.ble_capture: Optional[BLETsharkCapture] = None
        self.lora_driver = None

        self.started_monotonic = None
        self.started_datetime = None
        self.stop_reason = ""

        self.mode_simulation_var = tk.BooleanVar(value=True)
        self.mode_rf_var = tk.BooleanVar(value=False)
        self.mode_ble_var = tk.BooleanVar(value=False)
        self.mode_wifi_var = tk.BooleanVar(value=False)
        self.mode_lora_sim_var = tk.BooleanVar(value=False)
        self.mode_lora_real_var = tk.BooleanVar(value=False)

        self.vref_var = tk.StringVar(value="3.3")
        self.period_ms_var = tk.StringVar(value="100")
        self.spi_bus_var = tk.StringVar(value="0")
        self.spi_device_var = tk.StringVar(value="0")
        self.wifi_interface_var = tk.StringVar(value="wlan0")
        self.ble_interface_var = tk.StringVar(value="/dev/ttyUSB0-4.4")
        self.lora_port_var = tk.StringVar(value="/dev/ttyUSB1")
        self.lora_baud_var = tk.StringVar(value="115200")
        self.lora_frequency_var = tk.StringVar(value="868.1")
        self.lora_sf_var = tk.StringVar(value="7")
        self.lora_bw_var = tk.StringVar(value="125")
        self.lora_cr_var = tk.StringVar(value="4/5")
        self.lora_interval_var = tk.StringVar(value="1.0")

        self.acquisition_mode_var = tk.StringVar(value="Acquisition complète")
        self.duration_var = tk.StringVar(value="30")
        self.record_limit_var = tk.StringVar(value="1000")

        self.orange_threshold_var = tk.StringVar(value="-70")
        self.red_threshold_var = tk.StringVar(value="-50")

        self.status_var = tk.StringVar(value="Arrêtée")
        self.elapsed_var = tk.StringVar(value="0.0 s")
        self.total_energy_var = tk.StringVar(value="0 J")
        self.global_alert_var = tk.StringVar(value="VERT")
        self.progress_text_var = tk.StringVar(value="Prêt")
        self.graph_var = tk.StringVar(value="Puissance reçue")

        self.rf_chains = {
            "868 MHz": {
                "adc_channel": tk.StringVar(value="0"),
                "frequency": tk.StringVar(value="868"),
                "v_ref_detector": tk.StringVar(value="2.10"),
                "p_ref_detector": tk.StringVar(value="-40"),
                "slope": tk.StringVar(value="-0.025"),
                "gain": tk.StringVar(value="35"),
                "filter_loss": tk.StringVar(value="1.0"),
                "cable_loss": tk.StringVar(value="1.0"),
                "switch_loss": tk.StringVar(value="0"),
            },
            "2,45 GHz": {
                "adc_channel": tk.StringVar(value="1"),
                "frequency": tk.StringVar(value="2450"),
                "v_ref_detector": tk.StringVar(value="2.10"),
                "p_ref_detector": tk.StringVar(value="-40"),
                "slope": tk.StringVar(value="-0.025"),
                "gain": tk.StringVar(value="18.5"),
                "filter_loss": tk.StringVar(value="0.8"),
                "cable_loss": tk.StringVar(value="1.0"),
                "switch_loss": tk.StringVar(value="0"),
            },
        }

        self._build_ui()
        self.after(100, self._process_queue)
        self.after(200, self._refresh_dashboard)
        self.protocol("WM_DELETE_WINDOW", self.close_application)

    def _build_ui(self):
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
        self.sources_tab = ttk.Frame(notebook)
        self.calibration_tab = ttk.Frame(notebook)
        self.records_tab = ttk.Frame(notebook)
        self.devices_tab = ttk.Frame(notebook)
        self.lora_tab = ttk.Frame(notebook)
        self.graphs_tab = ttk.Frame(notebook)
        self.log_tab = ttk.Frame(notebook)

        notebook.add(self.dashboard_tab, text="Tableau de bord TEMPO")
        notebook.add(self.sources_tab, text="Sources / Acquisition")
        notebook.add(self.calibration_tab, text="Calibration RF")
        notebook.add(self.records_tab, text="Mesures")
        notebook.add(self.devices_tab, text="Appareils BLE")
        notebook.add(self.lora_tab, text="LoRa 868 MHz")
        notebook.add(self.graphs_tab, text="Graphiques")
        notebook.add(self.log_tab, text="Journal")

        self._build_dashboard()
        self._build_sources()
        self._build_calibration()
        self._build_records()
        self._build_devices()
        self._build_lora_tab()
        self._build_graphs()
        self._build_log()

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

        cards = ttk.Frame(self.dashboard_tab)
        cards.pack(fill="x", padx=12, pady=8)

        for i, (title, variable) in enumerate([
            ("Durée", self.elapsed_var),
            ("Énergie RF cumulée estimée", self.total_energy_var),
            ("État", self.status_var),
        ]):
            frame = ttk.LabelFrame(cards, text=title)
            frame.grid(row=0, column=i, sticky="nsew", padx=6)
            ttk.Label(
                frame,
                textvariable=variable,
                font=("TkDefaultFont", 15, "bold"),
            ).pack(padx=25, pady=18)
            cards.columnconfigure(i, weight=1)

        columns = (
            "source", "technology", "band", "device",
            "rssi", "voltage", "power", "energy", "alert",
        )
        self.dashboard_table = ttk.Treeview(
            self.dashboard_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "source": "Source",
            "technology": "Technologie",
            "band": "Bande",
            "device": "Appareil",
            "rssi": "RSSI (dBm)",
            "voltage": "Tension (V)",
            "power": "P antenne/reçue (dBm)",
            "energy": "Énergie cumulée (J)",
            "alert": "Alerte",
        }

        for column in columns:
            self.dashboard_table.heading(column, text=headings[column])
            self.dashboard_table.column(
                column,
                width=190 if column == "device" else 145,
                anchor="center",
            )

        for level, bg in (
            ("VERT", "#c8f2d3"),
            ("ORANGE", "#ffe0a3"),
            ("ROUGE", "#f6b2b2"),
        ):
            self.dashboard_table.tag_configure(level, background=bg)

        self.dashboard_table.pack(
            fill="both", expand=True, padx=12, pady=12
        )

    def _build_sources(self):
        source_frame = ttk.LabelFrame(
            self.sources_tab,
            text="Sources de données",
        )
        source_frame.pack(fill="x", padx=12, pady=12)

        choices = [
            ("Simulation RF", self.mode_simulation_var),
            ("Chaîne analogique réelle MCP3208", self.mode_rf_var),
            ("Sniffer BLE / AirPods / iBeacon", self.mode_ble_var),
            ("RSSI Wi-Fi de la liaison courante", self.mode_wifi_var),
            ("Simulation LoRa 868 MHz", self.mode_lora_sim_var),
            ("Module LoRa réel", self.mode_lora_real_var),
        ]

        for i, (label, var) in enumerate(choices):
            ttk.Checkbutton(
                source_frame,
                text=label,
                variable=var,
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=14, pady=8)

        config = ttk.LabelFrame(
            self.sources_tab,
            text="Interfaces",
        )
        config.pack(fill="x", padx=12, pady=8)
        config.columnconfigure(1, weight=1)

        fields = [
            ("Tension VREF MCP3208 (V)", self.vref_var),
            ("Période d'échantillonnage (ms)", self.period_ms_var),
            ("Bus SPI", self.spi_bus_var),
            ("Périphérique SPI", self.spi_device_var),
            ("Interface Wi-Fi", self.wifi_interface_var),
            ("Interface nRF Sniffer", self.ble_interface_var),
            ("Port série LoRa", self.lora_port_var),
            ("Débit série LoRa", self.lora_baud_var),
        ]

        for row, (label, var) in enumerate(fields):
            ttk.Label(config, text=label).grid(
                row=row, column=0, padx=8, pady=5, sticky="w"
            )
            ttk.Entry(config, textvariable=var).grid(
                row=row, column=1, padx=8, pady=5, sticky="ew"
            )

        acquisition = ttk.LabelFrame(
            self.sources_tab,
            text="Acquisition et alertes",
        )
        acquisition.pack(fill="x", padx=12, pady=8)
        acquisition.columnconfigure(1, weight=1)

        fields2 = [
            ("Mode", self.acquisition_mode_var),
            ("Durée limite (s)", self.duration_var),
            ("Nombre limite de mesures", self.record_limit_var),
            ("Seuil Vert → Orange (dBm)", self.orange_threshold_var),
            ("Seuil Orange → Rouge (dBm)", self.red_threshold_var),
        ]

        for row, (label, var) in enumerate(fields2):
            ttk.Label(acquisition, text=label).grid(
                row=row, column=0, padx=8, pady=5, sticky="w"
            )
            if label == "Mode":
                widget = ttk.Combobox(
                    acquisition,
                    textvariable=var,
                    values=(
                        "Acquisition complète",
                        "Durée limitée",
                        "Nombre de mesures",
                    ),
                    state="readonly",
                )
            else:
                widget = ttk.Entry(acquisition, textvariable=var)
            widget.grid(row=row, column=1, padx=8, pady=5, sticky="ew")

        buttons = ttk.Frame(self.sources_tab)
        buttons.pack(fill="x", padx=12, pady=12)

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
            self.sources_tab,
            maximum=100,
            orient="horizontal",
        )
        self.progress_bar.pack(fill="x", padx=12, pady=8)

        ttk.Label(
            self.sources_tab,
            textvariable=self.progress_text_var,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", padx=12, pady=5)

    def _build_calibration(self):
        for name, vars_ in self.rf_chains.items():
            frame = ttk.LabelFrame(
                self.calibration_tab,
                text=f"Chaîne {name}",
            )
            frame.pack(fill="x", padx=12, pady=8)
            frame.columnconfigure(1, weight=1)
            frame.columnconfigure(3, weight=1)

            fields = [
                ("Canal ADC", "adc_channel"),
                ("Fréquence (MHz)", "frequency"),
                ("Vref détecteur (V)", "v_ref_detector"),
                ("Pref détecteur (dBm)", "p_ref_detector"),
                ("Pente détecteur (V/dB)", "slope"),
                ("Gain LNA (dB)", "gain"),
                ("Perte filtre (dB)", "filter_loss"),
                ("Perte câble (dB)", "cable_loss"),
                ("Perte commutateur (dB)", "switch_loss"),
            ]

            for i, (label, key) in enumerate(fields):
                row = i // 2
                col = (i % 2) * 2
                ttk.Label(frame, text=label).grid(
                    row=row, column=col, padx=8, pady=5, sticky="w"
                )
                ttk.Entry(
                    frame,
                    textvariable=vars_[key],
                ).grid(
                    row=row, column=col + 1,
                    padx=8, pady=5, sticky="ew"
                )

        ttk.Button(
            self.calibration_tab,
            text="Afficher le bilan de puissance",
            command=self.show_power_budget,
        ).pack(pady=10)

        self.power_budget_text = tk.Text(
            self.calibration_tab,
            height=14,
        )
        self.power_budget_text.pack(
            fill="both", expand=True, padx=12, pady=10
        )

    def _build_records(self):
        columns = (
            "time", "source", "technology", "band", "device",
            "rssi", "adc", "voltage", "power", "energy",
            "length", "pdu", "channel", "alert", "simulated",
        )
        self.record_table = ttk.Treeview(
            self.records_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "time": "Heure",
            "source": "Source",
            "technology": "Technologie",
            "band": "Bande",
            "device": "Appareil",
            "rssi": "RSSI",
            "adc": "ADC",
            "voltage": "Tension",
            "power": "Puissance dBm",
            "energy": "Énergie J",
            "length": "Longueur",
            "pdu": "PDU",
            "channel": "Canal",
            "alert": "Alerte",
            "simulated": "Simulation",
        }

        for column in columns:
            self.record_table.heading(column, text=headings[column])
            self.record_table.column(
                column,
                width=180 if column == "device" else 105,
                anchor="center",
            )

        self.record_table.pack(
            fill="both", expand=True, padx=12, pady=12
        )

    def _build_devices(self):
        columns = (
            "device", "manufacturer", "type", "frames",
            "rssi", "length", "pdu", "channel", "last_seen",
        )
        self.device_table = ttk.Treeview(
            self.devices_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "device": "Adresse / appareil",
            "manufacturer": "Fabricant",
            "type": "Type détecté",
            "frames": "Trames",
            "rssi": "RSSI récent",
            "length": "Longueur",
            "pdu": "Type PDU",
            "channel": "Canal",
            "last_seen": "Dernière détection",
        }

        for column in columns:
            self.device_table.heading(column, text=headings[column])
            self.device_table.column(
                column,
                width=260 if column == "type" else 135,
                anchor="center",
            )

        self.device_table.pack(
            fill="both", expand=True, padx=12, pady=12
        )


    def _build_lora_tab(self):
        config = ttk.LabelFrame(self.lora_tab, text="Configuration LoRa 868 MHz")
        config.pack(fill="x", padx=12, pady=12)
        config.columnconfigure(1, weight=1)
        fields = [
            ("Fréquence (MHz)", self.lora_frequency_var),
            ("Spreading Factor", self.lora_sf_var),
            ("Largeur de bande (kHz)", self.lora_bw_var),
            ("Coding Rate", self.lora_cr_var),
            ("Intervalle simulation (s)", self.lora_interval_var),
            ("Port série réel", self.lora_port_var),
            ("Débit série", self.lora_baud_var),
        ]
        for row,(label,var) in enumerate(fields):
            ttk.Label(config,text=label).grid(row=row,column=0,padx=8,pady=5,sticky="w")
            ttk.Entry(config,textvariable=var).grid(row=row,column=1,padx=8,pady=5,sticky="ew")
        columns=("time","source","frequency","rssi","snr","sf","bw","cr","length","toa","crc","payload")
        self.lora_table=ttk.Treeview(self.lora_tab,columns=columns,show="headings")
        heads={"time":"Heure","source":"Source","frequency":"Fréquence MHz","rssi":"RSSI dBm","snr":"SNR dB","sf":"SF","bw":"BW kHz","cr":"CR","length":"Octets","toa":"Durée s","crc":"CRC","payload":"Payload hex"}
        for c in columns:
            self.lora_table.heading(c,text=heads[c]); self.lora_table.column(c,width=230 if c=="payload" else 110,anchor="center")
        self.lora_table.pack(fill="both",expand=True,padx=12,pady=12)

    def _build_graphs(self):
        controls = ttk.Frame(self.graphs_tab)
        controls.pack(fill="x", padx=12, pady=8)

        ttk.Combobox(
            controls,
            textvariable=self.graph_var,
            values=(
                "Puissance reçue",
                "RSSI",
                "Tension détecteur",
                "Énergie cumulée",
            ),
            state="readonly",
            width=28,
        ).pack(side="left", padx=8)

        ttk.Button(
            controls,
            text="Actualiser",
            command=self.draw_graph,
        ).pack(side="left")

        self.figure = Figure(figsize=(12, 7), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.axes = [self.axis]
        self.canvas = FigureCanvasTkAgg(
            self.figure,
            master=self.graphs_tab,
        )
        self.canvas.get_tk_widget().pack(
            fill="both", expand=True, padx=12, pady=12
        )

    def _build_log(self):
        self.log_text = tk.Text(self.log_tab)
        self.log_text.pack(fill="both", expand=True, padx=12, pady=12)

    def parse_chains(self) -> list[RFChain]:
        output = []
        for name, vars_ in self.rf_chains.items():
            output.append(
                RFChain(
                    name=name,
                    band=name,
                    adc_channel=int(vars_["adc_channel"].get()),
                    frequency_mhz=float(vars_["frequency"].get().replace(",", ".")),
                    v_ref_detector=float(vars_["v_ref_detector"].get().replace(",", ".")),
                    p_ref_detector_dbm=float(vars_["p_ref_detector"].get().replace(",", ".")),
                    slope_v_per_db=float(vars_["slope"].get().replace(",", ".")),
                    lna_gain_db=float(vars_["gain"].get().replace(",", ".")),
                    filter_loss_db=float(vars_["filter_loss"].get().replace(",", ".")),
                    cable_loss_db=float(vars_["cable_loss"].get().replace(",", ".")),
                    switch_loss_db=float(vars_["switch_loss"].get().replace(",", ".")),
                )
            )
        return output

    def start_acquisition(self):
        if self.running:
            messagebox.showwarning("TEMPO", "Une acquisition est déjà active.")
            return

        try:
            selected = any((
                self.mode_simulation_var.get(),
                self.mode_rf_var.get(),
                self.mode_ble_var.get(),
                self.mode_wifi_var.get(),
                self.mode_lora_sim_var.get(),
                self.mode_lora_real_var.get(),
            ))
            if not selected:
                raise ValueError("Sélectionnez au moins une source.")

            float(self.vref_var.get().replace(",", "."))
            period_ms = float(self.period_ms_var.get().replace(",", "."))
            if period_ms <= 0:
                raise ValueError("La période doit être positive.")

            orange = float(self.orange_threshold_var.get().replace(",", "."))
            red = float(self.red_threshold_var.get().replace(",", "."))
            if orange >= red:
                raise ValueError("Le seuil orange doit être inférieur au seuil rouge.")

            self.parse_chains()

            self.running = True
            self.stop_event.clear()
            self.started_monotonic = time.monotonic()
            self.started_datetime = datetime.now()
            self.stop_reason = ""
            self.status_var.set("Acquisition active")

            if self.mode_rf_var.get():
                self.mcp_reader = MCP3208Reader(
                    bus=int(self.spi_bus_var.get()),
                    device=int(self.spi_device_var.get()),
                )
                self.mcp_reader.open()
                self.log("MCP3208 ouvert.")

            if self.mode_ble_var.get():
                self.ble_capture = BLETsharkCapture(
                    interface=self.ble_interface_var.get().strip(),
                    on_frame=lambda frame: self.event_queue.put(("ble", frame)),
                    on_log=lambda msg: self.event_queue.put(("log", msg)),
                )
                self.ble_capture.start()


            if self.mode_lora_sim_var.get() and self.mode_lora_real_var.get():
                raise ValueError("Choisissez soit la simulation LoRa, soit le module LoRa réel.")

            if self.mode_lora_sim_var.get():
                self.lora_driver = LoRaSimulationDriver(
                    on_packet=lambda packet: self.event_queue.put(("lora", packet)),
                    on_log=lambda msg: self.event_queue.put(("log", msg)),
                    interval_s=float(self.lora_interval_var.get().replace(",", ".")),
                    frequency_mhz=float(self.lora_frequency_var.get().replace(",", ".")),
                    sf=int(self.lora_sf_var.get()),
                    bw_khz=float(self.lora_bw_var.get().replace(",", ".")),
                    cr=self.lora_cr_var.get().strip(),
                )
                self.lora_driver.start()

            if self.mode_lora_real_var.get():
                self.lora_driver = LoRaSerialJSONDriver(
                    port=self.lora_port_var.get().strip(),
                    baudrate=int(self.lora_baud_var.get()),
                    on_packet=lambda packet: self.event_queue.put(("lora", packet)),
                    on_log=lambda msg: self.event_queue.put(("log", msg)),
                )
                self.lora_driver.start()

            if self.mode_simulation_var.get() or self.mode_rf_var.get() or self.mode_wifi_var.get():
                worker = threading.Thread(
                    target=self._periodic_loop,
                    daemon=True,
                )
                self.worker_threads = [worker]
                worker.start()

            self.log("Acquisition multimode démarrée.")

        except Exception as exc:
            self.running = False
            if self.mcp_reader:
                self.mcp_reader.close()
                self.mcp_reader = None
            if self.ble_capture:
                self.ble_capture.stop()
                self.ble_capture = None
            messagebox.showerror("Erreur", str(exc))

    def _periodic_loop(self):
        try:
            period_s = float(self.period_ms_var.get().replace(",", ".")) / 1000.0
            vref = float(self.vref_var.get().replace(",", "."))
            chains = self.parse_chains()

            while not self.stop_event.is_set():
                loop_start = time.monotonic()
                elapsed = loop_start - self.started_monotonic

                if self.mode_simulation_var.get():
                    for chain in chains:
                        self._generate_rf_record(chain, elapsed, period_s, vref, simulated=True)

                if self.mode_rf_var.get():
                    for chain in chains:
                        self._generate_rf_record(chain, elapsed, period_s, vref, simulated=False)

                if self.mode_wifi_var.get():
                    try:
                        rssi = read_wifi_rssi(self.wifi_interface_var.get().strip())
                        self._generate_rssi_record(
                            source="Wi-Fi",
                            technology="Wi-Fi",
                            band="2,4/5 GHz",
                            device=self.wifi_interface_var.get().strip(),
                            rssi_dbm=rssi,
                            duration_s=period_s,
                            simulated=False,
                        )
                    except Exception as exc:
                        self.event_queue.put(("log", f"Wi-Fi : {exc}"))

                if self._auto_stop_condition(elapsed):
                    self.event_queue.put(("stop", "condition d'arrêt atteinte"))
                    break

                sleep_time = period_s - (time.monotonic() - loop_start)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as exc:
            self.event_queue.put(("log", f"Erreur périodique : {exc}"))
            self.event_queue.put(("stop", "erreur acquisition"))

    def _generate_rf_record(self, chain, elapsed, period_s, vref, simulated):
        if simulated:
            if chain.name == "868 MHz":
                antenna_power = -72 + 7 * math.sin(elapsed / 6) + random.gauss(0, 1.3)
            else:
                antenna_power = -66 + 5 * math.sin(elapsed / 8) + random.gauss(0, 1.1)

            detector_power = antenna_power + chain.net_gain_db
            voltage = chain.v_ref_detector + (
                detector_power - chain.p_ref_detector_dbm
            ) * chain.slope_v_per_db
            voltage = max(0.0, min(vref, voltage))
            adc_code = int(round(voltage / vref * ADC_MAX))
            source = "Simulation RF"
        else:
            adc_code = self.mcp_reader.read_channel(chain.adc_channel)
            voltage = adc_to_voltage(adc_code, vref)
            detector_power = detector_voltage_to_power(
                voltage,
                chain.v_ref_detector,
                chain.p_ref_detector_dbm,
                chain.slope_v_per_db,
            )
            antenna_power = detector_power - chain.net_gain_db
            source = "MCP3208"

        power_w = dbm_to_watts(antenna_power)
        energy_j = power_w * period_s
        key = f"{source}:{chain.name}"
        self.cumulative_energy[key] += energy_j

        record = Record(
            timestamp_epoch=time.time(),
            elapsed_s=elapsed,
            source=source,
            technology="Détection RF large bande",
            band=chain.band,
            device=chain.name,
            rssi_dbm=None,
            adc_code=adc_code,
            voltage_v=voltage,
            power_detector_dbm=detector_power,
            power_antenna_dbm=antenna_power,
            power_w=power_w,
            duration_s=period_s,
            energy_j=energy_j,
            cumulative_energy_j=self.cumulative_energy[key],
            packet_length_bytes=None,
            pdu_type="",
            channel=None,
            alert_level=alert_level(
                antenna_power,
                float(self.orange_threshold_var.get().replace(",", ".")),
                float(self.red_threshold_var.get().replace(",", ".")),
            ),
            simulated=simulated,
        )
        self.event_queue.put(("record", record))

    def _generate_rssi_record(
        self,
        source,
        technology,
        band,
        device,
        rssi_dbm,
        duration_s,
        simulated,
        packet_length=None,
        pdu_type="",
        channel=None,
    ):
        power_w = dbm_to_watts(rssi_dbm)
        energy_j = power_w * duration_s
        key = f"{source}:{device}"
        self.cumulative_energy[key] += energy_j

        record = Record(
            timestamp_epoch=time.time(),
            elapsed_s=time.monotonic() - self.started_monotonic,
            source=source,
            technology=technology,
            band=band,
            device=device,
            rssi_dbm=rssi_dbm,
            adc_code=None,
            voltage_v=None,
            power_detector_dbm=None,
            power_antenna_dbm=rssi_dbm,
            power_w=power_w,
            duration_s=duration_s,
            energy_j=energy_j,
            cumulative_energy_j=self.cumulative_energy[key],
            packet_length_bytes=packet_length,
            pdu_type=pdu_type,
            channel=channel,
            alert_level=alert_level(
                rssi_dbm,
                float(self.orange_threshold_var.get().replace(",", ".")),
                float(self.red_threshold_var.get().replace(",", ".")),
            ),
            simulated=simulated,
        )
        self.event_queue.put(("record", record))

    def _handle_ble_frame(self, frame: BLEFrame):
        if frame.rssi_dbm is None:
            return

        duration_s = (
            ((frame.length_bytes or 37) + 10) * 8e-6
        )
        device = frame.address or frame.device_type

        self._generate_rssi_record(
            source="BLE Sniffer",
            technology=frame.device_type,
            band="2,4 GHz BLE",
            device=device,
            rssi_dbm=frame.rssi_dbm,
            duration_s=duration_s,
            simulated=False,
            packet_length=frame.length_bytes,
            pdu_type=frame.pdu_type,
            channel=frame.channel,
        )


    def _handle_lora_packet(self, packet: LoRaPacket):
        try:
            cr_den = int(packet.coding_rate.split("/")[-1])
        except Exception:
            cr_den = 5
        duration = time_on_air_seconds(packet.payload_length, packet.spreading_factor, packet.bandwidth_khz, cr_den)
        metadata = f"SNR={packet.snr_db:.2f};SF={packet.spreading_factor};BW={packet.bandwidth_khz};CR={packet.coding_rate};F={packet.frequency_mhz:.3f};CRC={'OK' if packet.crc_ok else 'ERREUR'};PAYLOAD={packet.payload_hex[:80]}"
        self._generate_rssi_record(
            source=packet.source,
            technology="LoRa",
            band="868 MHz",
            device=f"LoRa {packet.frequency_mhz:.3f} MHz",
            rssi_dbm=packet.rssi_dbm,
            duration_s=duration,
            simulated=packet.source == "Simulation LoRa",
            packet_length=packet.payload_length,
            pdu_type=metadata,
            channel=packet.spreading_factor,
        )

    def _process_queue(self):
        while True:
            try:
                event_type, value = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "record":
                self.records.append(value)
                self._display_record(value)
            elif event_type == "ble":
                self._handle_ble_frame(value)
            elif event_type == "lora":
                self._handle_lora_packet(value)
            elif event_type == "log":
                self.log(value)
            elif event_type == "stop":
                self.stop_acquisition(value)

        self.after(100, self._process_queue)

    def _display_record(self, record: Record):
        self.record_table.insert(
            "",
            "end",
            values=(
                datetime.fromtimestamp(record.timestamp_epoch).strftime("%H:%M:%S.%f")[:-3],
                record.source,
                record.technology,
                record.band,
                record.device,
                "" if record.rssi_dbm is None else f"{record.rssi_dbm:.2f}",
                "" if record.adc_code is None else record.adc_code,
                "" if record.voltage_v is None else f"{record.voltage_v:.6f}",
                "" if record.power_antenna_dbm is None else f"{record.power_antenna_dbm:.3f}",
                f"{record.energy_j:.6e}",
                "" if record.packet_length_bytes is None else record.packet_length_bytes,
                record.pdu_type,
                "" if record.channel is None else record.channel,
                record.alert_level,
                "Oui" if record.simulated else "Non",
            ),
        )

        if len(self.record_table.get_children()) > 4000:
            self.record_table.delete(self.record_table.get_children()[0])

    def _auto_stop_condition(self, elapsed):
        mode = self.acquisition_mode_var.get()

        if mode == "Durée limitée":
            return elapsed >= float(self.duration_var.get().replace(",", "."))

        if mode == "Nombre de mesures":
            return len(self.records) >= int(self.record_limit_var.get())

        return False

    def stop_acquisition(self, reason):
        if not self.running:
            return

        self.running = False
        self.stop_reason = reason
        self.stop_event.set()

        if self.mcp_reader:
            self.mcp_reader.close()
            self.mcp_reader = None

        if self.ble_capture:
            self.ble_capture.stop()
            self.ble_capture = None

        if self.lora_driver:
            self.lora_driver.stop()
            self.lora_driver = None

        self.status_var.set("Arrêtée")
        self.log(f"Acquisition arrêtée : {reason}")

        if self.records:
            self.export_results()

    def clear_data(self):
        if self.running:
            messagebox.showwarning("TEMPO", "Arrêtez d'abord l'acquisition.")
            return

        self.records.clear()
        self.cumulative_energy.clear()
        self.record_table.delete(*self.record_table.get_children())
        self.dashboard_table.delete(*self.dashboard_table.get_children())
        self.device_table.delete(*self.device_table.get_children())
        self.lora_table.delete(*self.lora_table.get_children())
        self.total_energy_var.set("0 J")
        self.elapsed_var.set("0.0 s")
        self.global_alert_var.set("VERT")
        self.alert_banner.configure(bg=ALERT_COLORS["VERT"])
        self.progress_bar["value"] = 0
        self.progress_text_var.set("Prêt")
        self.figure.clear()
        self.axis = self.figure.add_subplot(111)
        self.axes = [self.axis]
        self.canvas.draw_idle()

    def _refresh_dashboard(self):
        elapsed = (
            time.monotonic() - self.started_monotonic
            if self.running and self.started_monotonic is not None
            else (self.records[-1].elapsed_s if self.records else 0.0)
        )
        self.elapsed_var.set(f"{elapsed:.1f} s")

        latest = {}
        for record in self.records:
            latest[(record.source, record.device)] = record

        self.dashboard_table.delete(*self.dashboard_table.get_children())

        priority = {"VERT": 0, "ORANGE": 1, "ROUGE": 2}
        global_level = "VERT"

        for _, record in sorted(latest.items()):
            if priority[record.alert_level] > priority[global_level]:
                global_level = record.alert_level

            self.dashboard_table.insert(
                "",
                "end",
                values=(
                    record.source,
                    record.technology,
                    record.band,
                    record.device,
                    "" if record.rssi_dbm is None else f"{record.rssi_dbm:.2f}",
                    "" if record.voltage_v is None else f"{record.voltage_v:.6f}",
                    "" if record.power_antenna_dbm is None else f"{record.power_antenna_dbm:.3f}",
                    f"{record.cumulative_energy_j:.6e}",
                    record.alert_level,
                ),
                tags=(record.alert_level,),
            )

        self.global_alert_var.set(global_level)
        self.alert_banner.configure(bg=ALERT_COLORS[global_level])
        self.total_energy_var.set(
            f"{sum(self.cumulative_energy.values()):.6e} J"
        )

        self._refresh_ble_devices()

        self.lora_table.delete(*self.lora_table.get_children())
        for record in [r for r in self.records if r.technology == "LoRa"][-1000:]:
            meta = {}
            for item in record.pdu_type.split(";"):
                if "=" in item:
                    key, value = item.split("=", 1); meta[key] = value
            self.lora_table.insert("", "end", values=(
                datetime.fromtimestamp(record.timestamp_epoch).strftime("%H:%M:%S.%f")[:-3],
                record.source, meta.get("F", ""),
                "" if record.rssi_dbm is None else f"{record.rssi_dbm:.2f}",
                meta.get("SNR", ""), meta.get("SF", ""), meta.get("BW", ""), meta.get("CR", ""),
                "" if record.packet_length_bytes is None else record.packet_length_bytes,
                f"{record.duration_s:.6f}", meta.get("CRC", ""), meta.get("PAYLOAD", ""),
            ))

        if self.running:
            mode = self.acquisition_mode_var.get()
            if mode == "Durée limitée":
                target = float(self.duration_var.get().replace(",", "."))
                percent = min(100.0, elapsed / target * 100.0)
                self.progress_bar["value"] = percent
                self.progress_text_var.set(
                    f"{elapsed:.1f}/{target:.1f} s — {percent:.1f} %"
                )
            elif mode == "Nombre de mesures":
                target = int(self.record_limit_var.get())
                percent = min(100.0, len(self.records) / target * 100.0)
                self.progress_bar["value"] = percent
                self.progress_text_var.set(
                    f"{len(self.records)}/{target} mesures — {percent:.1f} %"
                )
            else:
                self.progress_bar["value"] = 0
                self.progress_text_var.set(
                    f"Acquisition complète — {len(self.records)} mesures"
                )

        self.after(200, self._refresh_dashboard)

    def _refresh_ble_devices(self):
        self.device_table.delete(*self.device_table.get_children())
        self.lora_table.delete(*self.lora_table.get_children())

        grouped = defaultdict(list)
        for record in self.records:
            if record.source == "BLE Sniffer":
                grouped[record.device].append(record)

        for device in sorted(grouped):
            values = grouped[device]
            latest = values[-1]
            manufacturer = "Apple" if "Apple" in latest.technology or "AirPods" in latest.technology or latest.technology == "iBeacon" else "Inconnu"

            self.device_table.insert(
                "",
                "end",
                values=(
                    device,
                    manufacturer,
                    latest.technology,
                    len(values),
                    "" if latest.rssi_dbm is None else f"{latest.rssi_dbm:.2f}",
                    "" if latest.packet_length_bytes is None else latest.packet_length_bytes,
                    latest.pdu_type,
                    "" if latest.channel is None else latest.channel,
                    datetime.fromtimestamp(latest.timestamp_epoch).strftime("%H:%M:%S.%f")[:-3],
                ),
            )

    def show_power_budget(self):
        self.power_budget_text.delete("1.0", "end")

        for chain in self.parse_chains():
            self.power_budget_text.insert(
                "end",
                (
                    f"=== {chain.name} ===\n"
                    f"Gain LNA : +{chain.lna_gain_db:.2f} dB\n"
                    f"Perte filtre : -{chain.filter_loss_db:.2f} dB\n"
                    f"Perte câble : -{chain.cable_loss_db:.2f} dB\n"
                    f"Perte commutateur : -{chain.switch_loss_db:.2f} dB\n"
                    f"Gain net : {chain.net_gain_db:+.2f} dB\n\n"
                    f"P_detecteur = P_antenne + {chain.net_gain_db:.2f} dB\n"
                    f"P_antenne = P_detecteur - {chain.net_gain_db:.2f} dB\n\n"
                ),
            )

    @staticmethod
    def _graph_value(record: Record, graph: str):
        """Retourne la grandeur à tracer sans confondre RSSI et puissance RF."""
        if graph == "Puissance reçue":
            # Les sources numériques stockent aussi leur RSSI dans
            # power_antenna_dbm pour les alertes. Ce n'est pas une mesure de
            # puissance issue de la chaîne RF : on l'exclut de ce graphe.
            if record.source not in {"Simulation RF", "MCP3208"}:
                return None
            return record.power_antenna_dbm

        if graph == "RSSI":
            return record.rssi_dbm

        if graph == "Tension détecteur":
            return record.voltage_v

        if graph == "Énergie cumulée":
            return record.cumulative_energy_j

        return None

    @staticmethod
    def _graph_panel_key(record: Record):
        """Un panneau par source et, pour les voies RF, par bande."""
        if record.source == "Simulation RF":
            # Les deux bandes simulées sont volontairement superposées afin
            # de comparer directement leur évolution temporelle.
            return record.source, "868 MHz et 2,45 GHz", record.simulated
        return record.source, record.band, record.simulated

    @staticmethod
    def _graph_series_label(record: Record):
        """Conserve plusieurs appareils dans le panneau BLE sans les mélanger."""
        if record.source == "Simulation RF":
            return record.band

        if record.source == "BLE Sniffer":
            device = record.device or "appareil inconnu"
            if len(device) > 18:
                device = "…" + device[-17:]
            return f"{record.technology} | {device}"

        if record.technology == "LoRa":
            return record.device or "LoRa"

        return record.device or record.band or record.source

    def draw_graph(self):
        graph = self.graph_var.get()
        ylabel_by_graph = {
            "Puissance reçue": "Puissance à l'antenne (dBm)",
            "RSSI": "RSSI (dBm)",
            "Tension détecteur": "Tension (V)",
            "Énergie cumulée": "Énergie cumulée estimée (J)",
        }
        ylabel = ylabel_by_graph.get(graph, "Valeur")

        # Organisation : un panneau par source/bande, puis une courbe par
        # appareil à l'intérieur du panneau (utile pour le BLE).
        panels = defaultdict(lambda: defaultdict(list))
        for record in self.records:
            value = self._graph_value(record, graph)
            if value is None:
                continue
            panel_key = self._graph_panel_key(record)
            series_label = self._graph_series_label(record)
            panels[panel_key][series_label].append((record.elapsed_s, value))

        self.figure.clear()

        if not panels:
            self.axis = self.figure.add_subplot(111)
            self.axes = [self.axis]
            self.axis.set_title(graph)
            self.axis.text(
                0.5,
                0.5,
                "Aucune donnée compatible avec ce graphique",
                ha="center",
                va="center",
                transform=self.axis.transAxes,
            )
            self.axis.set_xlabel("Temps (s)")
            self.axis.set_ylabel(ylabel)
            self.axis.grid(True, alpha=0.3)
            self.figure.tight_layout()
            self.canvas.draw_idle()
            return

        panel_items = sorted(
            panels.items(),
            key=lambda item: (
                item[0][2],
                item[0][0].lower(),
                item[0][1].lower(),
            ),
        )
        panel_count = len(panel_items)
        columns = 1 if panel_count <= 3 else 2
        rows = math.ceil(panel_count / columns)
        share_y = graph in {"Puissance reçue", "RSSI", "Tension détecteur"}

        axes_grid = self.figure.subplots(
            rows,
            columns,
            squeeze=False,
            sharex=True,
            sharey=share_y,
        )
        all_axes = list(axes_grid.flat)
        self.axes = all_axes[:panel_count]
        self.axis = self.axes[0]

        # La légende BLE est placée au niveau de la figure, sous les subplots.
        # Elle ne peut ainsi recouvrir aucune courbe.
        ble_legend_handles = []
        ble_legend_labels = []

        for axis in all_axes[panel_count:]:
            axis.set_visible(False)

        orange = float(self.orange_threshold_var.get().replace(",", "."))
        red = float(self.red_threshold_var.get().replace(",", "."))

        for axis, (panel_key, series) in zip(self.axes, panel_items):
            source, band, simulated = panel_key
            status = "SIMULATION" if simulated else "RÉEL"
            axis.set_title(f"{source} — {band} [{status}]", fontsize=10, weight="bold")
            axis.set_facecolor("#fff4df" if simulated else "#eef7ff")

            series_items = sorted(
                series.items(),
                key=lambda item: (-len(item[1]), item[0].lower()),
            )
            if source == "BLE Sniffer":
                labelled_items = series_items[:MAX_BLE_LEGEND_SERIES]
                other_items = series_items[MAX_BLE_LEGEND_SERIES:]
            else:
                labelled_items = series_items
                other_items = []

            for label, points in labelled_items:
                points.sort(key=lambda point: point[0])
                x_values = [point[0] for point in points]
                y_values = [point[1] for point in points]
                line, = axis.plot(
                    x_values,
                    y_values,
                    linewidth=1.35,
                    linestyle="--" if simulated else "-",
                    marker="o" if len(points) <= 40 else None,
                    markersize=2.5,
                    label=label,
                )

                if source == "BLE Sniffer":
                    ble_legend_handles.append(line)
                    ble_legend_labels.append(label)

            # Les appareils BLE moins représentés restent visibles, avec une
            # couleur grise discrète et une seule entrée dans la légende.
            other_handle = None
            for label, points in other_items:
                points.sort(key=lambda point: point[0])
                x_values = [point[0] for point in points]
                y_values = [point[1] for point in points]
                line, = axis.plot(
                    x_values,
                    y_values,
                    color="#7f8c8d",
                    linewidth=0.9,
                    alpha=0.38,
                    linestyle="--" if simulated else "-",
                    marker="o" if len(points) <= 40 else None,
                    markersize=2.0,
                    label="_nolegend_",
                )
                if other_handle is None:
                    other_handle = line

            if source == "BLE Sniffer" and other_handle is not None:
                ble_legend_handles.append(other_handle)
                ble_legend_labels.append(
                    f"Autres appareils BLE ({len(other_items)})"
                )

            if graph in {"Puissance reçue", "RSSI"}:
                axis.axhline(orange, color="#ef8f00", linewidth=0.8, alpha=0.55)
                axis.axhline(red, color="#c62828", linewidth=0.8, alpha=0.55)

            if graph == "Énergie cumulée":
                axis.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))

            axis.set_xlabel("Temps (s)")
            axis.set_ylabel(ylabel)
            axis.grid(True, alpha=0.35)

            if source != "BLE Sniffer" and len(series) > 1:
                axis.legend(loc="best", fontsize=7, ncol=1)

        if graph == "Puissance reçue":
            subtitle = "Voies RF uniquement — le RSSI numérique n'est pas assimilé à une puissance calibrée"
        elif graph == "Énergie cumulée":
            subtitle = "Comparaison séparée par source — indicateurs RSSI et RF non additionnés visuellement"
        else:
            subtitle = "Courbe continue : donnée réelle | courbe pointillée : simulation"

        self.figure.suptitle(
            f"{graph} — évolution par source\n{subtitle}",
            fontsize=12,
            weight="bold",
        )

        bottom_margin = 0.02
        if ble_legend_handles:
            legend_columns = min(4, len(ble_legend_handles))
            self.figure.legend(
                ble_legend_handles,
                ble_legend_labels,
                loc="lower center",
                bbox_to_anchor=(0.5, 0.008),
                fontsize=7,
                ncol=legend_columns,
                title="Appareils BLE les plus observés",
                title_fontsize=8,
                frameon=True,
            )
            bottom_margin = 0.12

        self.figure.set_size_inches(12, max(6.5, rows * 3.2))
        self.figure.tight_layout(rect=(0, bottom_margin, 1, 0.93))
        self.canvas.draw_idle()

    def export_results(self):
        folder = OUTPUT_DIR / (
            "acquisition_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        graph_folder = folder / "graphes"
        graph_folder.mkdir(parents=True, exist_ok=True)

        rows = [record.as_dict() for record in self.records]
        with (folder / "mesures_tempo_v10.csv").open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=list(rows[0].keys()),
                delimiter=";",
            )
            writer.writeheader()
            writer.writerows(rows)

        with (folder / "synthese_tempo_v10.csv").open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["parametre", "valeur"])
            writer.writerow(["debut", self.started_datetime.isoformat() if self.started_datetime else ""])
            writer.writerow(["motif_arret", self.stop_reason])
            writer.writerow(["nombre_mesures", len(self.records)])
            writer.writerow(["energie_totale_j", sum(self.cumulative_energy.values())])
            writer.writerow(["simulation_activee", self.mode_simulation_var.get()])
            writer.writerow(["mcp3208_active", self.mode_rf_var.get()])
            writer.writerow(["ble_actif", self.mode_ble_var.get()])
            writer.writerow(["wifi_actif", self.mode_wifi_var.get()])
            writer.writerow(["lora_simulation_active", self.mode_lora_sim_var.get()])
            writer.writerow(["lora_reel_actif", self.mode_lora_real_var.get()])

        graphs = [
            ("Puissance reçue", "puissance_recue.png"),
            ("RSSI", "rssi.png"),
            ("Tension détecteur", "tension_detecteur.png"),
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

        self.log(f"Export terminé : {folder}")
        messagebox.showinfo("Export terminé", str(folder))

    def log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")

    def close_application(self):
        if self.running:
            self.stop_event.set()

        if self.mcp_reader:
            try:
                self.mcp_reader.close()
            except Exception:
                pass

        if self.ble_capture:
            try:
                self.ble_capture.stop()
            except Exception:
                pass

        if self.lora_driver:
            try:
                self.lora_driver.stop()
            except Exception:
                pass

        self.destroy()


if __name__ == "__main__":
    TempoV10().mainloop()
