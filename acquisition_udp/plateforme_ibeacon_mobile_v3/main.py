#!/usr/bin/env python3
from __future__ import annotations

import csv
import queue
import re
import shutil
import subprocess
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Optional
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

APP_TITLE = "Plateforme iBeacon V3 — Analyse multi-UUID"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "acquisitions_ibeacon_v3"

DEFAULT_INTERFACE = "/dev/ttyUSB0-4.4"
DEFAULT_GROUP_WINDOW_MS = 20.0
IBEACON_BODY_HEX_LEN = 46
MAX_RAW_LINES = 1000


@dataclass
class IBeaconFrame:
    timestamp: float
    address: str
    pdu_type: str
    length: Optional[int]
    rssi: Optional[float]
    channel: Optional[int]
    uuid: str
    major: int
    minor: int
    tx_power: int
    raw_hex: str
    source_field: str

    def as_dict(self):
        return asdict(self)


@dataclass
class IBeaconEvent:
    index: int
    uuid: str
    major: int
    minor: int
    address: str
    start_epoch: float
    end_epoch: float
    duration_ms: float
    interval_ms: Optional[float]
    packet_count: int
    channels: str
    rssi_mean: Optional[float]
    tx_power: int

    def as_dict(self):
        return asdict(self)


def clean_hex(value: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", value or "").lower()


def signed8(value: int) -> int:
    return value - 256 if value > 127 else value


def format_uuid(raw: bytes) -> str:
    h = raw.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def decode_ibeacon(value: str):
    h = clean_hex(value)
    if len(h) < IBEACON_BODY_HEX_LEN:
        return None

    positions = []

    for marker in ("4c000215", "004c0215"):
        pos = h.find(marker)
        if pos >= 0:
            positions.append(pos + 4)

    search_start = 0
    while True:
        pos = h.find("0215", search_start)
        if pos < 0:
            break
        positions.append(pos)
        search_start = pos + 2

    for pos in dict.fromkeys(positions):
        body = h[pos:pos + IBEACON_BODY_HEX_LEN]

        if len(body) != IBEACON_BODY_HEX_LEN:
            continue

        if not body.startswith("0215"):
            continue

        try:
            raw = bytes.fromhex(body)
        except ValueError:
            continue

        if len(raw) != 23:
            continue

        return {
            "uuid": format_uuid(raw[2:18]),
            "major": int.from_bytes(raw[18:20], "big"),
            "minor": int.from_bytes(raw[20:22], "big"),
            "tx_power": signed8(raw[22]),
            "raw_hex": body,
        }

    return None


def parse_float(value):
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def parse_int(value):
    try:
        return int(str(value), 0)
    except Exception:
        try:
            return int(str(value))
        except Exception:
            return None


class NRFCapture:
    METADATA_FIELDS = [
        "frame.time_epoch",
        "btle.advertising_address",
        "btle.advertising_header.pdu_type",
        "btle.length",
        "nordic_ble.rssi",
        "nordic_ble.channel",
    ]

    RAW_FIELD_CANDIDATES = [
        "btcommon.eir_ad.entry.data",
        "btcommon.eir_ad.entry.service_data",
        "btle.advertising_data",
        "btle.data",
        "data.data",
    ]

    def __init__(self, interface, on_frame, on_log, on_raw):
        self.interface = interface
        self.on_frame = on_frame
        self.on_log = on_log
        self.on_raw = on_raw

        self.process = None
        self.stop_event = threading.Event()
        self.metadata_fields = []
        self.raw_fields = []

    @staticmethod
    def list_interfaces():
        result = subprocess.run(
            ["tshark", "-D"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        interfaces = []

        for line in result.stdout.splitlines():
            if ". " in line:
                interfaces.append(line.split(". ", 1)[1].strip())

        return interfaces

    @staticmethod
    def available_fields():
        result = subprocess.run(
            ["tshark", "-G", "fields"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        fields = set()

        for line in result.stdout.splitlines():
            parts = line.split("\t")

            if len(parts) >= 3 and parts[0] == "F":
                fields.add(parts[2])

        return fields

    def build_command(self):
        available = self.available_fields()

        self.metadata_fields = [
            field
            for field in self.METADATA_FIELDS
            if field in available
        ]

        self.raw_fields = [
            field
            for field in self.RAW_FIELD_CANDIDATES
            if field in available
        ]

        if "frame.time_epoch" not in self.metadata_fields:
            raise RuntimeError("Le champ frame.time_epoch est indisponible.")

        if not self.raw_fields:
            raise RuntimeError(
                "Aucun champ brut BLE compatible n'a été trouvé dans tshark."
            )

        command = [
            "tshark",
            "-l",
            "-n",
            "-i",
            self.interface,
            "-Y",
            "btle",
            "-T",
            "fields",
            "-E",
            "separator=;",
            "-E",
            "occurrence=a",
            "-E",
            "aggregator=|",
            "-E",
            "quote=n",
        ]

        for field in self.metadata_fields + self.raw_fields:
            command.extend(["-e", field])

        return command

    def start(self):
        if self.process is not None:
            raise RuntimeError("Une capture est déjà active.")

        if shutil.which("tshark") is None:
            raise RuntimeError("tshark est introuvable.")

        if not self.interface.strip():
            raise ValueError("L'interface nRF Sniffer est vide.")

        command = self.build_command()

        self.on_log("Commande : " + " ".join(command))
        self.on_log("Champs bruts : " + ", ".join(self.raw_fields))

        self.stop_event.clear()

        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        threading.Thread(
            target=self._read_stdout,
            daemon=True,
        ).start()

        threading.Thread(
            target=self._read_stderr,
            daemon=True,
        ).start()

    def _read_stdout(self):
        assert self.process is not None
        assert self.process.stdout is not None

        fields = self.metadata_fields + self.raw_fields
        index = {field: i for i, field in enumerate(fields)}

        for line in self.process.stdout:
            if self.stop_event.is_set():
                break

            line = line.rstrip("\n")

            if not line:
                continue

            parts = line.split(";")
            parts += [""] * (len(fields) - len(parts))

            timestamp = parse_float(parts[index["frame.time_epoch"]])

            if timestamp is None:
                continue

            decoded = None
            selected_field = ""

            for field in self.raw_fields:
                value = parts[index[field]]

                for candidate in [
                    item
                    for item in value.split("|")
                    if item.strip()
                ]:
                    decoded = decode_ibeacon(candidate)

                    if decoded is not None:
                        selected_field = field
                        break

                if decoded is not None:
                    break

            if decoded is None:
                diagnostic = " || ".join(
                    f"{field}={parts[index[field]]}"
                    for field in self.raw_fields
                    if parts[index[field]]
                )

                if diagnostic:
                    self.on_raw(diagnostic)

                continue

            frame = IBeaconFrame(
                timestamp=timestamp,
                address=(
                    parts[index["btle.advertising_address"]]
                    if "btle.advertising_address" in index
                    else ""
                ),
                pdu_type=(
                    parts[index["btle.advertising_header.pdu_type"]]
                    if "btle.advertising_header.pdu_type" in index
                    else ""
                ),
                length=(
                    parse_int(parts[index["btle.length"]])
                    if "btle.length" in index
                    else None
                ),
                rssi=(
                    parse_float(parts[index["nordic_ble.rssi"]])
                    if "nordic_ble.rssi" in index
                    else None
                ),
                channel=(
                    parse_int(parts[index["nordic_ble.channel"]])
                    if "nordic_ble.channel" in index
                    else None
                ),
                uuid=decoded["uuid"],
                major=decoded["major"],
                minor=decoded["minor"],
                tx_power=decoded["tx_power"],
                raw_hex=decoded["raw_hex"],
                source_field=selected_field,
            )

            self.on_frame(frame)

    def _read_stderr(self):
        assert self.process is not None
        assert self.process.stderr is not None

        for line in self.process.stderr:
            message = line.strip()

            if message:
                self.on_log("tshark : " + message)

    def stop(self):
        self.stop_event.set()

        if self.process is not None:
            self.process.terminate()

            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

        self.process = None


def group_events(frames, window_ms):
    if not frames:
        return []

    groups = []

    for frame in sorted(frames, key=lambda item: item.timestamp):
        identity = (
            frame.uuid,
            frame.major,
            frame.minor,
            frame.address,
        )

        if not groups:
            groups.append([frame])
            continue

        previous = groups[-1][-1]

        previous_identity = (
            previous.uuid,
            previous.major,
            previous.minor,
            previous.address,
        )

        gap_ms = (
            frame.timestamp - previous.timestamp
        ) * 1000.0

        if (
            identity == previous_identity
            and gap_ms <= window_ms
        ):
            groups[-1].append(frame)
        else:
            groups.append([frame])

    previous_start = {}
    events = []

    for index, group in enumerate(groups, start=1):
        first = group[0]
        last = group[-1]

        identity = (
            first.uuid,
            first.major,
            first.minor,
            first.address,
        )

        interval_ms = None

        if identity in previous_start:
            interval_ms = (
                first.timestamp - previous_start[identity]
            ) * 1000.0

        previous_start[identity] = first.timestamp

        channels = sorted({
            frame.channel
            for frame in group
            if frame.channel is not None
        })

        rssi_values = [
            frame.rssi
            for frame in group
            if frame.rssi is not None
        ]

        events.append(
            IBeaconEvent(
                index=index,
                uuid=first.uuid,
                major=first.major,
                minor=first.minor,
                address=first.address,
                start_epoch=first.timestamp,
                end_epoch=last.timestamp,
                duration_ms=(
                    last.timestamp - first.timestamp
                ) * 1000.0,
                interval_ms=interval_ms,
                packet_count=len(group),
                channels=",".join(
                    str(channel)
                    for channel in channels
                ),
                rssi_mean=(
                    mean(rssi_values)
                    if rssi_values
                    else None
                ),
                tx_power=first.tx_power,
            )
        )

    return events


def calculate_global_statistics(frames, events):
    rssi_values = [
        frame.rssi
        for frame in frames
        if frame.rssi is not None
    ]

    intervals = [
        event.interval_ms
        for event in events
        if event.interval_ms is not None
    ]

    durations = [
        event.duration_ms
        for event in events
    ]

    return {
        "nombre_trames_ibeacon": len(frames),
        "nombre_evenements": len(events),
        "nombre_uuid_uniques": len({
            frame.uuid
            for frame in frames
        }),
        "nombre_ibeacons_uniques": len({
            (
                frame.uuid,
                frame.major,
                frame.minor,
            )
            for frame in frames
        }),
        "rssi_moyen_dbm": (
            mean(rssi_values)
            if rssi_values
            else None
        ),
        "rssi_mediane_dbm": (
            median(rssi_values)
            if rssi_values
            else None
        ),
        "rssi_min_dbm": (
            min(rssi_values)
            if rssi_values
            else None
        ),
        "rssi_max_dbm": (
            max(rssi_values)
            if rssi_values
            else None
        ),
        "rssi_ecart_type_db": (
            pstdev(rssi_values)
            if len(rssi_values) > 1
            else (
                0.0
                if rssi_values
                else None
            )
        ),
        "intervalle_moyen_ms": (
            mean(intervals)
            if intervals
            else None
        ),
        "intervalle_mediane_ms": (
            median(intervals)
            if intervals
            else None
        ),
        "duree_evenement_moyenne_ms": (
            mean(durations)
            if durations
            else None
        ),
        "canal_37": sum(
            1
            for frame in frames
            if frame.channel == 37
        ),
        "canal_38": sum(
            1
            for frame in frames
            if frame.channel == 38
        ),
        "canal_39": sum(
            1
            for frame in frames
            if frame.channel == 39
        ),
    }


def calculate_statistics_by_uuid(frames, events):
    frames_by_uuid = defaultdict(list)
    events_by_uuid = defaultdict(list)

    for frame in frames:
        frames_by_uuid[frame.uuid].append(frame)

    for event in events:
        events_by_uuid[event.uuid].append(event)

    rows = []

    for uuid in sorted(frames_by_uuid):
        uuid_frames = frames_by_uuid[uuid]
        uuid_events = events_by_uuid.get(uuid, [])

        rssi_values = [
            frame.rssi
            for frame in uuid_frames
            if frame.rssi is not None
        ]

        intervals = [
            event.interval_ms
            for event in uuid_events
            if event.interval_ms is not None
        ]

        channel_37 = sum(
            1
            for frame in uuid_frames
            if frame.channel == 37
        )
        channel_38 = sum(
            1
            for frame in uuid_frames
            if frame.channel == 38
        )
        channel_39 = sum(
            1
            for frame in uuid_frames
            if frame.channel == 39
        )

        rows.append({
            "uuid": uuid,
            "nombre_trames": len(uuid_frames),
            "nombre_evenements": len(uuid_events),
            "rssi_moyen_dbm": (
                mean(rssi_values)
                if rssi_values
                else None
            ),
            "rssi_mediane_dbm": (
                median(rssi_values)
                if rssi_values
                else None
            ),
            "rssi_min_dbm": (
                min(rssi_values)
                if rssi_values
                else None
            ),
            "rssi_max_dbm": (
                max(rssi_values)
                if rssi_values
                else None
            ),
            "rssi_ecart_type_db": (
                pstdev(rssi_values)
                if len(rssi_values) > 1
                else (
                    0.0
                    if rssi_values
                    else None
                )
            ),
            "intervalle_moyen_ms": (
                mean(intervals)
                if intervals
                else None
            ),
            "intervalle_mediane_ms": (
                median(intervals)
                if intervals
                else None
            ),
            "canal_37": channel_37,
            "canal_38": channel_38,
            "canal_39": channel_39,
        })

    return rows


class IBeaconApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1450x930")
        self.minsize(1150, 750)

        self.frames = []
        self.events = []
        self.capture = None
        self.event_queue = queue.Queue()
        self.raw_count = 0

        self.interface_var = tk.StringVar(
            value=DEFAULT_INTERFACE
        )
        self.window_var = tk.StringVar(
            value=str(DEFAULT_GROUP_WINDOW_MS)
        )
        self.uuid_filter_var = tk.StringVar()
        self.major_filter_var = tk.StringVar()
        self.minor_filter_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Capture arrêtée"
        )
        self.frame_count_var = tk.StringVar(
            value="Trames iBeacon : 0"
        )
        self.event_count_var = tk.StringVar(
            value="Événements : 0"
        )
        self.uuid_count_var = tk.StringVar(
            value="UUID détectés : 0"
        )
        self.raw_count_var = tk.StringVar(
            value="Paquets BLE inspectés : 0"
        )
        self.graph_var = tk.StringVar(
            value="RSSI par UUID"
        )

        self._build_interface()
        self.refresh_interfaces()

        self.after(
            100,
            self._process_queue,
        )

        self.protocol(
            "WM_DELETE_WINDOW",
            self.close_application,
        )

    def _build_interface(self):
        header = ttk.Frame(self)
        header.pack(
            fill="x",
            padx=12,
            pady=10,
        )

        ttk.Label(
            header,
            text="Plateforme iBeacon V3 — analyse multi-UUID",
            font=("TkDefaultFont", 16, "bold"),
        ).pack(side="left")

        ttk.Label(
            header,
            textvariable=self.status_var,
        ).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=8,
        )

        self.capture_tab = ttk.Frame(notebook)
        self.analysis_tab = ttk.Frame(notebook)
        self.uuid_stats_tab = ttk.Frame(notebook)
        self.graph_tab = ttk.Frame(notebook)
        self.raw_tab = ttk.Frame(notebook)
        self.log_tab = ttk.Frame(notebook)

        notebook.add(
            self.capture_tab,
            text="Détection",
        )
        notebook.add(
            self.analysis_tab,
            text="Événements",
        )
        notebook.add(
            self.uuid_stats_tab,
            text="Statistiques par UUID",
        )
        notebook.add(
            self.graph_tab,
            text="Graphiques",
        )
        notebook.add(
            self.raw_tab,
            text="Diagnostic brut",
        )
        notebook.add(
            self.log_tab,
            text="Journal",
        )

        self._build_capture_tab()
        self._build_analysis_tab()
        self._build_uuid_statistics_tab()
        self._build_graph_tab()
        self._build_raw_tab()
        self._build_log_tab()

    def _build_capture_tab(self):
        config = ttk.LabelFrame(
            self.capture_tab,
            text="Configuration",
        )
        config.pack(
            fill="x",
            padx=12,
            pady=12,
        )
        config.columnconfigure(1, weight=1)

        ttk.Label(
            config,
            text="Interface nRF Sniffer",
        ).grid(
            row=0,
            column=0,
            padx=8,
            pady=5,
        )

        self.interface_combo = ttk.Combobox(
            config,
            textvariable=self.interface_var,
        )
        self.interface_combo.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=8,
            pady=5,
        )

        ttk.Button(
            config,
            text="Actualiser",
            command=self.refresh_interfaces,
        ).grid(
            row=0,
            column=2,
            padx=8,
        )

        entries = [
            (
                "Fenêtre de regroupement (ms)",
                self.window_var,
            ),
            (
                "Filtre UUID facultatif",
                self.uuid_filter_var,
            ),
            (
                "Filtre Major facultatif",
                self.major_filter_var,
            ),
            (
                "Filtre Minor facultatif",
                self.minor_filter_var,
            ),
        ]

        for row, (label, variable) in enumerate(
            entries,
            start=1,
        ):
            ttk.Label(
                config,
                text=label,
            ).grid(
                row=row,
                column=0,
                padx=8,
                pady=5,
            )

            ttk.Entry(
                config,
                textvariable=variable,
            ).grid(
                row=row,
                column=1,
                columnspan=2,
                sticky="ew",
                padx=8,
                pady=5,
            )

        buttons = ttk.Frame(config)
        buttons.grid(
            row=5,
            column=0,
            columnspan=3,
            pady=10,
        )

        ttk.Button(
            buttons,
            text="Démarrer",
            command=self.start_capture,
        ).pack(
            side="left",
            padx=5,
        )

        ttk.Button(
            buttons,
            text="Arrêter et sauvegarder",
            command=self.stop_capture,
        ).pack(
            side="left",
            padx=5,
        )

        ttk.Button(
            buttons,
            text="Effacer",
            command=self.clear_data,
        ).pack(
            side="left",
            padx=5,
        )

        counters = ttk.Frame(self.capture_tab)
        counters.pack(
            fill="x",
            padx=12,
        )

        for variable in (
            self.frame_count_var,
            self.event_count_var,
            self.uuid_count_var,
            self.raw_count_var,
        ):
            ttk.Label(
                counters,
                textvariable=variable,
                font=("TkDefaultFont", 10, "bold"),
            ).pack(
                side="left",
                padx=12,
            )

        columns = (
            "time",
            "uuid",
            "major",
            "minor",
            "tx",
            "address",
            "rssi",
            "channel",
            "source",
        )

        self.frame_table = ttk.Treeview(
            self.capture_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "time": "Temps epoch",
            "uuid": "UUID",
            "major": "Major",
            "minor": "Minor",
            "tx": "Tx Power",
            "address": "Adresse",
            "rssi": "RSSI",
            "channel": "Canal",
            "source": "Champ tshark",
        }

        widths = {
            "time": 145,
            "uuid": 300,
            "major": 65,
            "minor": 65,
            "tx": 75,
            "address": 145,
            "rssi": 75,
            "channel": 65,
            "source": 210,
        }

        for column in columns:
            self.frame_table.heading(
                column,
                text=headings[column],
            )
            self.frame_table.column(
                column,
                width=widths[column],
                anchor="center",
            )

        self.frame_table.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_analysis_tab(self):
        ttk.Button(
            self.analysis_tab,
            text="Recalculer",
            command=self.analyse,
        ).pack(
            anchor="w",
            padx=12,
            pady=10,
        )

        self.global_statistics_text = tk.Text(
            self.analysis_tab,
            height=12,
        )
        self.global_statistics_text.pack(
            fill="x",
            padx=12,
            pady=(0, 10),
        )

        columns = (
            "index",
            "uuid",
            "major",
            "minor",
            "duration",
            "interval",
            "packets",
            "channels",
            "rssi",
        )

        self.event_table = ttk.Treeview(
            self.analysis_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "index": "N°",
            "uuid": "UUID",
            "major": "Major",
            "minor": "Minor",
            "duration": "Durée (ms)",
            "interval": "Intervalle (ms)",
            "packets": "Paquets",
            "channels": "Canaux",
            "rssi": "RSSI moyen",
        }

        for column in columns:
            self.event_table.heading(
                column,
                text=headings[column],
            )
            self.event_table.column(
                column,
                width=300 if column == "uuid" else 120,
                anchor="center",
            )

        self.event_table.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_uuid_statistics_tab(self):
        columns = (
            "uuid",
            "frames",
            "events",
            "rssi_mean",
            "rssi_median",
            "rssi_min",
            "rssi_max",
            "interval_mean",
            "channel_37",
            "channel_38",
            "channel_39",
        )

        self.uuid_statistics_table = ttk.Treeview(
            self.uuid_stats_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "uuid": "UUID",
            "frames": "Trames",
            "events": "Événements",
            "rssi_mean": "RSSI moyen",
            "rssi_median": "RSSI médian",
            "rssi_min": "RSSI min",
            "rssi_max": "RSSI max",
            "interval_mean": "Intervalle moyen",
            "channel_37": "Canal 37",
            "channel_38": "Canal 38",
            "channel_39": "Canal 39",
        }

        for column in columns:
            self.uuid_statistics_table.heading(
                column,
                text=headings[column],
            )
            self.uuid_statistics_table.column(
                column,
                width=300 if column == "uuid" else 110,
                anchor="center",
            )

        self.uuid_statistics_table.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_graph_tab(self):
        controls = ttk.Frame(self.graph_tab)
        controls.pack(
            fill="x",
            padx=12,
            pady=8,
        )

        ttk.Label(
            controls,
            text="Graphe :",
        ).pack(
            side="left",
        )

        selector = ttk.Combobox(
            controls,
            textvariable=self.graph_var,
            values=(
                "RSSI par UUID",
                "Intervalles par UUID",
                "Durées",
                "Canaux",
            ),
            state="readonly",
            width=24,
        )
        selector.pack(
            side="left",
            padx=8,
        )
        selector.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.draw_graph(),
        )

        ttk.Button(
            controls,
            text="Actualiser",
            command=self.draw_graph,
        ).pack(
            side="left",
        )

        self.figure = Figure(
            figsize=(11, 7),
            dpi=100,
        )
        self.axis = self.figure.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(
            self.figure,
            master=self.graph_tab,
        )
        self.canvas.get_tk_widget().pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_raw_tab(self):
        self.raw_text = tk.Text(
            self.raw_tab,
            wrap="none",
        )
        self.raw_text.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def _build_log_tab(self):
        self.log_text = tk.Text(
            self.log_tab,
        )
        self.log_text.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def refresh_interfaces(self):
        try:
            interfaces = NRFCapture.list_interfaces()

            self.interface_combo["values"] = interfaces

            matches = [
                value
                for value in interfaces
                if "nrf" in value.lower()
                or "ttyusb" in value.lower()
            ]

            if matches:
                self.interface_var.set(
                    matches[0].split(" (", 1)[0]
                )

            self.log("Interfaces tshark actualisées.")

        except Exception as exc:
            self.log(
                "Erreur interfaces : "
                + str(exc)
            )

    def queue_frame(self, frame):
        self.event_queue.put(
            ("frame", frame)
        )

    def queue_log(self, message):
        self.event_queue.put(
            ("log", message)
        )

    def queue_raw(self, message):
        self.event_queue.put(
            ("raw", message)
        )

    def _process_queue(self):
        while True:
            try:
                event_type, value = (
                    self.event_queue.get_nowait()
                )
            except queue.Empty:
                break

            if event_type == "frame":
                self.receive_frame(value)

            elif event_type == "log":
                self.log(value)

            elif event_type == "raw":
                self.raw_count += 1

                self.raw_count_var.set(
                    f"Paquets BLE inspectés : {self.raw_count}"
                )

                if self.raw_count <= MAX_RAW_LINES:
                    self.raw_text.insert(
                        "end",
                        value + "\n",
                    )
                    self.raw_text.see("end")

        self.after(
            100,
            self._process_queue,
        )

    def matches_filters(self, frame):
        uuid_filter = (
            self.uuid_filter_var
            .get()
            .strip()
            .lower()
        )

        major_filter = (
            self.major_filter_var
            .get()
            .strip()
        )

        minor_filter = (
            self.minor_filter_var
            .get()
            .strip()
        )

        if (
            uuid_filter
            and frame.uuid.lower() != uuid_filter
        ):
            return False

        if (
            major_filter
            and str(frame.major) != major_filter
        ):
            return False

        if (
            minor_filter
            and str(frame.minor) != minor_filter
        ):
            return False

        return True

    def receive_frame(self, frame):
        if not self.matches_filters(frame):
            return

        self.frames.append(frame)

        self.frame_count_var.set(
            f"Trames iBeacon : {len(self.frames)}"
        )

        self.uuid_count_var.set(
            "UUID détectés : "
            + str(len({
                item.uuid
                for item in self.frames
            }))
        )

        self.frame_table.insert(
            "",
            "end",
            values=(
                f"{frame.timestamp:.6f}",
                frame.uuid,
                frame.major,
                frame.minor,
                frame.tx_power,
                frame.address,
                (
                    ""
                    if frame.rssi is None
                    else f"{frame.rssi:.1f}"
                ),
                (
                    ""
                    if frame.channel is None
                    else frame.channel
                ),
                frame.source_field,
            ),
        )

        if len(self.frames) % 20 == 0:
            self.analyse()

    def start_capture(self):
        if self.capture is not None:
            messagebox.showwarning(
                "Capture",
                "Une capture est déjà active.",
            )
            return

        try:
            self.capture = NRFCapture(
                self.interface_var.get().strip(),
                self.queue_frame,
                self.queue_log,
                self.queue_raw,
            )

            self.capture.start()

            self.status_var.set(
                "Détection active"
            )

            self.log(
                "Capture démarrée."
            )

        except Exception as exc:
            self.capture = None

            self.status_var.set(
                "Capture arrêtée"
            )

            messagebox.showerror(
                "Erreur",
                str(exc),
            )

    def stop_capture(self):
        if self.capture is not None:
            self.capture.stop()
            self.capture = None

        self.status_var.set(
            "Capture arrêtée"
        )

        self.analyse()

        if self.frames:
            self.export_results()
        else:
            messagebox.showwarning(
                "Aucun iBeacon",
                (
                    "Aucun iBeacon reconnu. "
                    "Consultez l'onglet Diagnostic brut."
                ),
            )

    def clear_data(self):
        if self.capture is not None:
            messagebox.showwarning(
                "Capture",
                "Arrêtez d'abord la capture.",
            )
            return

        self.frames.clear()
        self.events.clear()
        self.raw_count = 0

        self.frame_count_var.set(
            "Trames iBeacon : 0"
        )
        self.event_count_var.set(
            "Événements : 0"
        )
        self.uuid_count_var.set(
            "UUID détectés : 0"
        )
        self.raw_count_var.set(
            "Paquets BLE inspectés : 0"
        )

        self.raw_text.delete(
            "1.0",
            "end",
        )
        self.global_statistics_text.delete(
            "1.0",
            "end",
        )

        for table in (
            self.frame_table,
            self.event_table,
            self.uuid_statistics_table,
        ):
            for item in table.get_children():
                table.delete(item)

        self.axis.clear()
        self.canvas.draw_idle()

    def analyse(self):
        try:
            window_ms = float(
                self.window_var
                .get()
                .replace(",", ".")
            )

            if window_ms <= 0:
                raise ValueError(
                    "La fenêtre de regroupement doit être positive."
                )

            self.events = group_events(
                self.frames,
                window_ms,
            )

        except Exception as exc:
            messagebox.showerror(
                "Analyse",
                str(exc),
            )
            return

        global_stats = calculate_global_statistics(
            self.frames,
            self.events,
        )

        uuid_stats = calculate_statistics_by_uuid(
            self.frames,
            self.events,
        )

        self.event_count_var.set(
            f"Événements : {len(self.events)}"
        )

        self.global_statistics_text.delete(
            "1.0",
            "end",
        )

        for key, value in global_stats.items():
            display = (
                f"{value:.4f}"
                if isinstance(value, float)
                else str(value)
            )

            self.global_statistics_text.insert(
                "end",
                f"{key} : {display}\n",
            )

        for item in self.event_table.get_children():
            self.event_table.delete(item)

        for event in self.events:
            self.event_table.insert(
                "",
                "end",
                values=(
                    event.index,
                    event.uuid,
                    event.major,
                    event.minor,
                    f"{event.duration_ms:.3f}",
                    (
                        ""
                        if event.interval_ms is None
                        else f"{event.interval_ms:.3f}"
                    ),
                    event.packet_count,
                    event.channels,
                    (
                        ""
                        if event.rssi_mean is None
                        else f"{event.rssi_mean:.2f}"
                    ),
                ),
            )

        for item in self.uuid_statistics_table.get_children():
            self.uuid_statistics_table.delete(item)

        for row in uuid_stats:
            self.uuid_statistics_table.insert(
                "",
                "end",
                values=(
                    row["uuid"],
                    row["nombre_trames"],
                    row["nombre_evenements"],
                    self.format_number(
                        row["rssi_moyen_dbm"]
                    ),
                    self.format_number(
                        row["rssi_mediane_dbm"]
                    ),
                    self.format_number(
                        row["rssi_min_dbm"]
                    ),
                    self.format_number(
                        row["rssi_max_dbm"]
                    ),
                    self.format_number(
                        row["intervalle_moyen_ms"]
                    ),
                    row["canal_37"],
                    row["canal_38"],
                    row["canal_39"],
                ),
            )

        self.draw_graph()

    @staticmethod
    def format_number(value):
        if value is None:
            return ""

        return f"{value:.2f}"

    def draw_graph(self):
        self.axis.clear()

        graph_name = self.graph_var.get()

        if graph_name == "RSSI par UUID":
            grouped = defaultdict(list)

            for frame in self.frames:
                if frame.rssi is not None:
                    grouped[frame.uuid].append(frame)

            if grouped:
                global_start = min(
                    frame.timestamp
                    for frames in grouped.values()
                    for frame in frames
                )

                for uuid in sorted(grouped):
                    uuid_frames = sorted(
                        grouped[uuid],
                        key=lambda item: item.timestamp,
                    )

                    self.axis.plot(
                        [
                            frame.timestamp - global_start
                            for frame in uuid_frames
                        ],
                        [
                            frame.rssi
                            for frame in uuid_frames
                        ],
                        marker="o",
                        markersize=3,
                        linewidth=1,
                        label=uuid,
                    )

                self.axis.set_xlabel(
                    "Temps (s)"
                )
                self.axis.set_ylabel(
                    "RSSI (dBm)"
                )
                self.axis.set_title(
                    "RSSI différencié par UUID"
                )
                self.axis.legend(
                    loc="best",
                    fontsize=8,
                )

        elif graph_name == "Intervalles par UUID":
            grouped = defaultdict(list)

            for event in self.events:
                if event.interval_ms is not None:
                    grouped[event.uuid].append(event)

            for uuid in sorted(grouped):
                values = grouped[uuid]

                self.axis.plot(
                    range(1, len(values) + 1),
                    [
                        event.interval_ms
                        for event in values
                    ],
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=uuid,
                )

            self.axis.set_xlabel(
                "Événement de l'UUID"
            )
            self.axis.set_ylabel(
                "Intervalle (ms)"
            )
            self.axis.set_title(
                "Intervalles différenciés par UUID"
            )

            if grouped:
                self.axis.legend(
                    loc="best",
                    fontsize=8,
                )

        elif graph_name == "Durées":
            grouped = defaultdict(list)

            for event in self.events:
                grouped[event.uuid].append(
                    event.duration_ms
                )

            for uuid in sorted(grouped):
                values = grouped[uuid]

                self.axis.hist(
                    values,
                    bins=min(
                        20,
                        max(5, len(values)),
                    ),
                    alpha=0.5,
                    label=uuid,
                )

            self.axis.set_xlabel(
                "Durée (ms)"
            )
            self.axis.set_ylabel(
                "Nombre"
            )
            self.axis.set_title(
                "Histogrammes des durées par UUID"
            )

            if grouped:
                self.axis.legend(
                    loc="best",
                    fontsize=8,
                )

        elif graph_name == "Canaux":
            uuid_list = sorted({
                frame.uuid
                for frame in self.frames
            })

            if uuid_list:
                x_positions = list(
                    range(len(uuid_list))
                )

                width = 0.25

                counts_37 = [
                    sum(
                        1
                        for frame in self.frames
                        if (
                            frame.uuid == uuid
                            and frame.channel == 37
                        )
                    )
                    for uuid in uuid_list
                ]

                counts_38 = [
                    sum(
                        1
                        for frame in self.frames
                        if (
                            frame.uuid == uuid
                            and frame.channel == 38
                        )
                    )
                    for uuid in uuid_list
                ]

                counts_39 = [
                    sum(
                        1
                        for frame in self.frames
                        if (
                            frame.uuid == uuid
                            and frame.channel == 39
                        )
                    )
                    for uuid in uuid_list
                ]

                self.axis.bar(
                    [
                        position - width
                        for position in x_positions
                    ],
                    counts_37,
                    width=width,
                    label="Canal 37",
                )

                self.axis.bar(
                    x_positions,
                    counts_38,
                    width=width,
                    label="Canal 38",
                )

                self.axis.bar(
                    [
                        position + width
                        for position in x_positions
                    ],
                    counts_39,
                    width=width,
                    label="Canal 39",
                )

                self.axis.set_xticks(
                    x_positions
                )
                self.axis.set_xticklabels(
                    [
                        uuid[:8] + "…"
                        for uuid in uuid_list
                    ],
                    rotation=45,
                    ha="right",
                )

                self.axis.set_xlabel(
                    "UUID"
                )
                self.axis.set_ylabel(
                    "Nombre de trames"
                )
                self.axis.set_title(
                    "Répartition des canaux par UUID"
                )
                self.axis.legend()

        self.axis.grid(True)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def export_results(self):
        folder = OUTPUT_DIR / (
            "acquisition_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
        )

        graph_folder = folder / "graphes"
        graph_folder.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.write_rows(
            folder / "trames_ibeacon.csv",
            [
                frame.as_dict()
                for frame in self.frames
            ],
        )

        self.write_rows(
            folder / "evenements_ibeacon.csv",
            [
                event.as_dict()
                for event in self.events
            ],
        )

        self.write_statistics(
            folder / "statistiques_globales.csv",
            calculate_global_statistics(
                self.frames,
                self.events,
            ),
        )

        self.write_rows(
            folder / "statistiques_par_uuid.csv",
            calculate_statistics_by_uuid(
                self.frames,
                self.events,
            ),
        )

        self.save_graphs(
            graph_folder,
        )

        self.log(
            "Export terminé : "
            + str(folder)
        )

        messagebox.showinfo(
            "Export terminé",
            str(folder),
        )

    @staticmethod
    def write_rows(path, rows):
        if not rows:
            path.write_text(
                "",
                encoding="utf-8",
            )
            return

        with path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=list(
                    rows[0].keys()
                ),
                delimiter=";",
            )
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def write_statistics(path, values):
        with path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.writer(
                file,
                delimiter=";",
            )
            writer.writerow([
                "indicateur",
                "valeur",
            ])
            writer.writerows(
                values.items()
            )

    def save_graphs(self, folder):
        frames_by_uuid = defaultdict(list)

        for frame in self.frames:
            if frame.rssi is not None:
                frames_by_uuid[frame.uuid].append(
                    frame
                )

        if frames_by_uuid:
            figure, axis = plt.subplots()

            global_start = min(
                frame.timestamp
                for frames in frames_by_uuid.values()
                for frame in frames
            )

            for uuid in sorted(frames_by_uuid):
                uuid_frames = sorted(
                    frames_by_uuid[uuid],
                    key=lambda item: item.timestamp,
                )

                axis.plot(
                    [
                        frame.timestamp - global_start
                        for frame in uuid_frames
                    ],
                    [
                        frame.rssi
                        for frame in uuid_frames
                    ],
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=uuid,
                )

            axis.set_xlabel(
                "Temps (s)"
            )
            axis.set_ylabel(
                "RSSI (dBm)"
            )
            axis.set_title(
                "RSSI différencié par UUID"
            )
            axis.legend(
                loc="best",
                fontsize=8,
            )
            axis.grid(True)
            figure.tight_layout()
            figure.savefig(
                folder / "rssi_par_uuid.png",
                dpi=180,
            )
            plt.close(figure)

        intervals_by_uuid = defaultdict(list)

        for event in self.events:
            if event.interval_ms is not None:
                intervals_by_uuid[event.uuid].append(
                    event.interval_ms
                )

        if intervals_by_uuid:
            figure, axis = plt.subplots()

            for uuid in sorted(intervals_by_uuid):
                values = intervals_by_uuid[uuid]

                axis.plot(
                    range(1, len(values) + 1),
                    values,
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=uuid,
                )

            axis.set_xlabel(
                "Événement de l'UUID"
            )
            axis.set_ylabel(
                "Intervalle (ms)"
            )
            axis.set_title(
                "Intervalles différenciés par UUID"
            )
            axis.legend(
                loc="best",
                fontsize=8,
            )
            axis.grid(True)
            figure.tight_layout()
            figure.savefig(
                folder / "intervalles_par_uuid.png",
                dpi=180,
            )
            plt.close(figure)

        durations_by_uuid = defaultdict(list)

        for event in self.events:
            durations_by_uuid[event.uuid].append(
                event.duration_ms
            )

        if durations_by_uuid:
            figure, axis = plt.subplots()

            for uuid in sorted(durations_by_uuid):
                values = durations_by_uuid[uuid]

                axis.hist(
                    values,
                    bins=min(
                        20,
                        max(5, len(values)),
                    ),
                    alpha=0.5,
                    label=uuid,
                )

            axis.set_xlabel(
                "Durée (ms)"
            )
            axis.set_ylabel(
                "Nombre"
            )
            axis.set_title(
                "Durées par UUID"
            )
            axis.legend(
                loc="best",
                fontsize=8,
            )
            axis.grid(True)
            figure.tight_layout()
            figure.savefig(
                folder / "durees_par_uuid.png",
                dpi=180,
            )
            plt.close(figure)

    def log(self, message):
        self.log_text.insert(
            "end",
            str(message) + "\n",
        )
        self.log_text.see(
            "end"
        )

    def close_application(self):
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass

        self.destroy()


if __name__ == "__main__":
    IBeaconApp().mainloop()
