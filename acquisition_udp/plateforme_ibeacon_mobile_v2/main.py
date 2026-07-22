#!/usr/bin/env python3
from __future__ import annotations

import csv
import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Optional
import tkinter as tk
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

APP_TITLE = "Plateforme iBeacon V2 — nRF Connect Mobile"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "acquisitions_ibeacon_v2"
DEFAULT_INTERFACE = "/dev/ttyUSB0-4.4"
DEFAULT_GROUP_WINDOW_MS = 20.0

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
    if len(h) < 50:
        return None

    starts = []
    for marker in ("4c000215", "004c0215"):
        pos = h.find(marker)
        if pos >= 0:
            starts.append(pos + 4)

    pos = h.find("0215")
    if pos >= 0:
        starts.append(pos)

    for start in starts:
        body = h[start:start + 50]
        if len(body) < 50 or not body.startswith("0215"):
            continue
        try:
            raw = bytes.fromhex(body)
            return {
                "uuid": format_uuid(raw[2:18]),
                "major": int.from_bytes(raw[18:20], "big"),
                "minor": int.from_bytes(raw[20:22], "big"),
                "tx_power": signed8(raw[22]),
                "raw_hex": h,
            }
        except Exception:
            continue
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
    METADATA = [
        "frame.time_epoch",
        "btle.advertising_address",
        "btle.advertising_header.pdu_type",
        "btle.length",
        "nordic_ble.rssi",
        "nordic_ble.channel",
    ]
    RAW_CANDIDATES = [
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
        self.metadata = []
        self.raw_fields = []

    @staticmethod
    def list_interfaces():
        result = subprocess.run(
            ["tshark", "-D"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        values = []
        for line in result.stdout.splitlines():
            if ". " in line:
                values.append(line.split(". ", 1)[1].strip())
        return values

    @staticmethod
    def available_fields():
        result = subprocess.run(
            ["tshark", "-G", "fields"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        fields = set()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0] == "F":
                fields.add(parts[2])
        return fields

    def command(self):
        available = self.available_fields()
        self.metadata = [field for field in self.METADATA if field in available]
        self.raw_fields = [field for field in self.RAW_CANDIDATES if field in available]

        if "frame.time_epoch" not in self.metadata:
            raise RuntimeError("frame.time_epoch est indisponible dans tshark.")
        if not self.raw_fields:
            raise RuntimeError(
                "Aucun champ brut BLE compatible n'a été trouvé dans tshark."
            )

        command = [
            "tshark", "-l", "-n", "-i", self.interface,
            "-Y", "btle",
            "-T", "fields",
            "-E", "separator=;",
            "-E", "occurrence=a",
            "-E", "aggregator=|",
            "-E", "quote=n",
        ]
        for field in self.metadata + self.raw_fields:
            command.extend(["-e", field])
        return command

    def start(self):
        if self.process is not None:
            raise RuntimeError("Une capture est déjà active.")
        if shutil.which("tshark") is None:
            raise RuntimeError("tshark est introuvable.")
        if not self.interface.strip():
            raise ValueError("L'interface nRF Sniffer est vide.")

        command = self.command()
        self.on_log("Commande : " + " ".join(command))
        self.on_log("Champs bruts utilisés : " + ", ".join(self.raw_fields))

        self.stop_event.clear()
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        assert self.process and self.process.stdout
        fields = self.metadata + self.raw_fields
        idx = {field: i for i, field in enumerate(fields)}

        for line in self.process.stdout:
            if self.stop_event.is_set():
                break
            raw_line = line.rstrip("\n")
            if not raw_line:
                continue

            parts = raw_line.split(";")
            parts += [""] * (len(fields) - len(parts))

            timestamp = parse_float(parts[idx["frame.time_epoch"]])
            if timestamp is None:
                continue

            decoded = None
            selected_raw = ""
            selected_field = ""

            for field in self.raw_fields:
                value = parts[idx[field]]
                for candidate in [item for item in value.split("|") if item]:
                    decoded = decode_ibeacon(candidate)
                    if decoded:
                        selected_raw = candidate
                        selected_field = field
                        break
                if decoded:
                    break

            if not decoded:
                diagnostic = " || ".join(
                    f"{field}={parts[idx[field]]}"
                    for field in self.raw_fields
                    if parts[idx[field]]
                )
                if diagnostic:
                    self.on_raw(diagnostic)
                continue

            self.on_frame(
                IBeaconFrame(
                    timestamp=timestamp,
                    address=parts[idx["btle.advertising_address"]]
                    if "btle.advertising_address" in idx else "",
                    pdu_type=parts[idx["btle.advertising_header.pdu_type"]]
                    if "btle.advertising_header.pdu_type" in idx else "",
                    length=parse_int(parts[idx["btle.length"]])
                    if "btle.length" in idx else None,
                    rssi=parse_float(parts[idx["nordic_ble.rssi"]])
                    if "nordic_ble.rssi" in idx else None,
                    channel=parse_int(parts[idx["nordic_ble.channel"]])
                    if "nordic_ble.channel" in idx else None,
                    uuid=decoded["uuid"],
                    major=decoded["major"],
                    minor=decoded["minor"],
                    tx_power=decoded["tx_power"],
                    raw_hex=selected_raw,
                    source_field=selected_field,
                )
            )

    def _read_stderr(self):
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            text = line.strip()
            if text:
                self.on_log("tshark : " + text)

    def stop(self):
        self.stop_event.set()
        if self.process:
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
    for frame in sorted(frames, key=lambda f: f.timestamp):
        key = (frame.uuid, frame.major, frame.minor)
        if not groups:
            groups.append([frame])
            continue

        previous = groups[-1][-1]
        previous_key = (previous.uuid, previous.major, previous.minor)
        gap_ms = (frame.timestamp - previous.timestamp) * 1000.0

        if key == previous_key and gap_ms <= window_ms:
            groups[-1].append(frame)
        else:
            groups.append([frame])

    last_start = {}
    events = []

    for index, group in enumerate(groups, start=1):
        first, last = group[0], group[-1]
        key = (first.uuid, first.major, first.minor)
        interval = None
        if key in last_start:
            interval = (first.timestamp - last_start[key]) * 1000.0
        last_start[key] = first.timestamp

        channels = sorted({
            frame.channel for frame in group if frame.channel is not None
        })
        rssi_values = [
            frame.rssi for frame in group if frame.rssi is not None
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
                duration_ms=(last.timestamp - first.timestamp) * 1000.0,
                interval_ms=interval,
                packet_count=len(group),
                channels=",".join(str(ch) for ch in channels),
                rssi_mean=mean(rssi_values) if rssi_values else None,
                tx_power=first.tx_power,
            )
        )
    return events

def calculate_stats(frames, events):
    rssi = [frame.rssi for frame in frames if frame.rssi is not None]
    intervals = [event.interval_ms for event in events if event.interval_ms is not None]
    durations = [event.duration_ms for event in events]
    expected = len(events) * 3
    received = sum(min(event.packet_count, 3) for event in events)

    return {
        "nombre_trames_ibeacon": len(frames),
        "nombre_evenements": len(events),
        "nombre_ibeacons_uniques": len({
            (f.uuid, f.major, f.minor) for f in frames
        }),
        "rssi_moyen_dbm": mean(rssi) if rssi else None,
        "rssi_min_dbm": min(rssi) if rssi else None,
        "rssi_max_dbm": max(rssi) if rssi else None,
        "rssi_ecart_type_db": pstdev(rssi) if len(rssi) > 1 else (0.0 if rssi else None),
        "intervalle_moyen_ms": mean(intervals) if intervals else None,
        "duree_evenement_moyenne_ms": mean(durations) if durations else None,
        "taux_perte_estime_pct": None if expected == 0 else max(
            0.0, 100.0 * (expected - received) / expected
        ),
        "canal_37": sum(1 for f in frames if f.channel == 37),
        "canal_38": sum(1 for f in frames if f.channel == 38),
        "canal_39": sum(1 for f in frames if f.channel == 39),
    }

class IBeaconV2App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1400x900")

        self.frames = []
        self.events = []
        self.capture = None
        self.queue = queue.Queue()
        self.raw_count = 0

        self.interface_var = tk.StringVar(value=DEFAULT_INTERFACE)
        self.window_var = tk.StringVar(value=str(DEFAULT_GROUP_WINDOW_MS))
        self.uuid_var = tk.StringVar()
        self.major_var = tk.StringVar()
        self.minor_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Capture arrêtée")
        self.frames_var = tk.StringVar(value="Trames iBeacon : 0")
        self.events_var = tk.StringVar(value="Événements : 0")
        self.raw_var = tk.StringVar(value="Paquets BLE inspectés : 0")
        self.graph_var = tk.StringVar(value="RSSI")

        self._build()
        self.refresh_interfaces()
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def _build(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=10)
        ttk.Label(
            header,
            text="Plateforme iBeacon V2 — téléphone nRF Connect Mobile",
            font=("TkDefaultFont", 16, "bold"),
        ).pack(side="left")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=8)

        self.detect_tab = ttk.Frame(notebook)
        self.analysis_tab = ttk.Frame(notebook)
        self.graph_tab = ttk.Frame(notebook)
        self.raw_tab = ttk.Frame(notebook)
        self.log_tab = ttk.Frame(notebook)

        notebook.add(self.detect_tab, text="Détection")
        notebook.add(self.analysis_tab, text="Analyse")
        notebook.add(self.graph_tab, text="Graphiques")
        notebook.add(self.raw_tab, text="Diagnostic brut")
        notebook.add(self.log_tab, text="Journal")

        self._build_detect()
        self._build_analysis()
        self._build_graph()
        self._build_raw()
        self._build_log()

    def _build_detect(self):
        box = ttk.LabelFrame(self.detect_tab, text="Configuration")
        box.pack(fill="x", padx=12, pady=12)
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Interface nRF Sniffer").grid(row=0, column=0, padx=8, pady=5)
        self.interface_combo = ttk.Combobox(box, textvariable=self.interface_var)
        self.interface_combo.grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(box, text="Actualiser", command=self.refresh_interfaces).grid(row=0, column=2, padx=8)

        fields = [
            ("Fenêtre de regroupement (ms)", self.window_var),
            ("Filtre UUID facultatif", self.uuid_var),
            ("Filtre Major facultatif", self.major_var),
            ("Filtre Minor facultatif", self.minor_var),
        ]
        for row, (label, var) in enumerate(fields, start=1):
            ttk.Label(box, text=label).grid(row=row, column=0, padx=8, pady=5)
            ttk.Entry(box, textvariable=var).grid(row=row, column=1, columnspan=2, sticky="ew", padx=8, pady=5)

        buttons = ttk.Frame(box)
        buttons.grid(row=5, column=0, columnspan=3, pady=10)
        ttk.Button(buttons, text="Démarrer", command=self.start_capture).pack(side="left", padx=5)
        ttk.Button(buttons, text="Arrêter et sauvegarder", command=self.stop_capture).pack(side="left", padx=5)
        ttk.Button(buttons, text="Effacer", command=self.clear_data).pack(side="left", padx=5)

        counters = ttk.Frame(self.detect_tab)
        counters.pack(fill="x", padx=12)
        for var in (self.frames_var, self.events_var, self.raw_var):
            ttk.Label(counters, textvariable=var, font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=14)

        cols = ("time", "uuid", "major", "minor", "tx", "address", "rssi", "channel", "source")
        self.frame_table = ttk.Treeview(self.detect_tab, columns=cols, show="headings")
        labels = {
            "time": "Temps epoch", "uuid": "UUID", "major": "Major", "minor": "Minor",
            "tx": "Tx Power", "address": "Adresse", "rssi": "RSSI",
            "channel": "Canal", "source": "Champ tshark"
        }
        widths = {
            "time": 145, "uuid": 300, "major": 65, "minor": 65,
            "tx": 75, "address": 145, "rssi": 75, "channel": 65, "source": 210
        }
        for col in cols:
            self.frame_table.heading(col, text=labels[col])
            self.frame_table.column(col, width=widths[col], anchor="center")
        self.frame_table.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_analysis(self):
        ttk.Button(self.analysis_tab, text="Recalculer", command=self.analyse).pack(anchor="w", padx=12, pady=10)
        self.stats_text = tk.Text(self.analysis_tab, height=14)
        self.stats_text.pack(fill="x", padx=12, pady=(0, 10))
        cols = ("index", "uuid", "major", "minor", "duration", "interval", "packets", "channels", "rssi")
        self.event_table = ttk.Treeview(self.analysis_tab, columns=cols, show="headings")
        for col in cols:
            self.event_table.heading(col, text=col)
            self.event_table.column(col, width=130 if col != "uuid" else 300, anchor="center")
        self.event_table.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_graph(self):
        top = ttk.Frame(self.graph_tab)
        top.pack(fill="x", padx=12, pady=8)
        selector = ttk.Combobox(
            top,
            textvariable=self.graph_var,
            values=("RSSI", "Intervalles", "Durées", "Canaux"),
            state="readonly",
        )
        selector.pack(side="left")
        selector.bind("<<ComboboxSelected>>", lambda _e: self.draw_graph())
        self.figure = Figure(figsize=(10, 6), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.graph_tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)

    def _build_raw(self):
        self.raw_text = tk.Text(self.raw_tab, wrap="none")
        self.raw_text.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_log(self):
        self.log_text = tk.Text(self.log_tab)
        self.log_text.pack(fill="both", expand=True, padx=12, pady=12)

    def refresh_interfaces(self):
        try:
            values = NRFCapture.list_interfaces()
            self.interface_combo["values"] = values
            matches = [v for v in values if "nrf" in v.lower() or "ttyusb" in v.lower()]
            if matches:
                self.interface_var.set(matches[0].split(" (", 1)[0])
            self.log("Interfaces actualisées.")
        except Exception as exc:
            self.log(str(exc))

    def queue_frame(self, frame):
        self.queue.put(("frame", frame))

    def queue_log(self, text):
        self.queue.put(("log", text))

    def queue_raw(self, text):
        self.queue.put(("raw", text))

    def _drain(self):
        while True:
            try:
                kind, value = self.queue.get_nowait()
            except queue.Empty:
                break

            if kind == "frame":
                self.accept_frame(value)
            elif kind == "log":
                self.log(value)
            elif kind == "raw":
                self.raw_count += 1
                self.raw_var.set(f"Paquets BLE inspectés : {self.raw_count}")
                if self.raw_count <= 500:
                    self.raw_text.insert("end", value + "\n")
        self.after(100, self._drain)

    def accept_frame(self, frame):
        if self.uuid_var.get().strip() and frame.uuid.lower() != self.uuid_var.get().strip().lower():
            return
        if self.major_var.get().strip() and str(frame.major) != self.major_var.get().strip():
            return
        if self.minor_var.get().strip() and str(frame.minor) != self.minor_var.get().strip():
            return

        self.frames.append(frame)
        self.frames_var.set(f"Trames iBeacon : {len(self.frames)}")
        self.frame_table.insert(
            "", "end",
            values=(
                f"{frame.timestamp:.6f}", frame.uuid, frame.major, frame.minor,
                frame.tx_power, frame.address,
                "" if frame.rssi is None else f"{frame.rssi:.1f}",
                "" if frame.channel is None else frame.channel,
                frame.source_field,
            )
        )
        if len(self.frames) % 20 == 0:
            self.analyse()

    def start_capture(self):
        if self.capture:
            return
        try:
            self.capture = NRFCapture(
                self.interface_var.get().strip(),
                self.queue_frame,
                self.queue_log,
                self.queue_raw,
            )
            self.capture.start()
            self.status_var.set("Détection active")
        except Exception as exc:
            self.capture = None
            messagebox.showerror("Erreur", str(exc))

    def stop_capture(self):
        if self.capture:
            self.capture.stop()
            self.capture = None
        self.status_var.set("Capture arrêtée")
        self.analyse()
        if self.frames:
            self.export_results()
        else:
            messagebox.showwarning(
                "Aucun iBeacon",
                "Aucun iBeacon reconnu. Consultez l'onglet Diagnostic brut."
            )

    def clear_data(self):
        self.frames.clear()
        self.events.clear()
        self.raw_count = 0
        self.frames_var.set("Trames iBeacon : 0")
        self.events_var.set("Événements : 0")
        self.raw_var.set("Paquets BLE inspectés : 0")
        self.raw_text.delete("1.0", "end")
        self.stats_text.delete("1.0", "end")
        for table in (self.frame_table, self.event_table):
            for item in table.get_children():
                table.delete(item)

    def analyse(self):
        try:
            window = float(self.window_var.get().replace(",", "."))
            self.events = group_events(self.frames, window)
        except Exception as exc:
            messagebox.showerror("Analyse", str(exc))
            return

        stats = calculate_stats(self.frames, self.events)
        self.events_var.set(f"Événements : {len(self.events)}")
        self.stats_text.delete("1.0", "end")
        for key, value in stats.items():
            display = f"{value:.4f}" if isinstance(value, float) else str(value)
            self.stats_text.insert("end", f"{key} : {display}\n")

        for item in self.event_table.get_children():
            self.event_table.delete(item)

        for event in self.events:
            self.event_table.insert(
                "", "end",
                values=(
                    event.index, event.uuid, event.major, event.minor,
                    f"{event.duration_ms:.3f}",
                    "" if event.interval_ms is None else f"{event.interval_ms:.3f}",
                    event.packet_count, event.channels,
                    "" if event.rssi_mean is None else f"{event.rssi_mean:.2f}",
                )
            )
        self.draw_graph()

    def draw_graph(self):
        self.axis.clear()
        name = self.graph_var.get()
        if name == "RSSI":
            valid = [f for f in self.frames if f.rssi is not None]
            if valid:
                t0 = valid[0].timestamp
                self.axis.plot([f.timestamp - t0 for f in valid], [f.rssi for f in valid])
                self.axis.set_xlabel("Temps (s)")
                self.axis.set_ylabel("RSSI (dBm)")
        elif name == "Intervalles":
            values = [e.interval_ms for e in self.events if e.interval_ms is not None]
            if values:
                self.axis.plot(range(1, len(values) + 1), values, marker="o")
                self.axis.set_ylabel("Intervalle (ms)")
        elif name == "Durées":
            values = [e.duration_ms for e in self.events]
            if values:
                self.axis.hist(values, bins=min(20, max(5, len(values))))
        elif name == "Canaux":
            counts = [sum(1 for f in self.frames if f.channel == ch) for ch in (37, 38, 39)]
            self.axis.bar(["37", "38", "39"], counts)
        self.axis.grid(True)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def export_results(self):
        folder = OUTPUT_DIR / ("acquisition_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        graph_dir = folder / "graphes"
        graph_dir.mkdir(parents=True, exist_ok=True)

        self.write_rows(folder / "trames_ibeacon.csv", [f.as_dict() for f in self.frames])
        self.write_rows(folder / "evenements_ibeacon.csv", [e.as_dict() for e in self.events])
        self.write_stats(folder / "statistiques_ibeacon.csv", calculate_stats(self.frames, self.events))
        self.save_graphs(graph_dir)

        self.log("Export : " + str(folder))
        messagebox.showinfo("Export terminé", str(folder))

    @staticmethod
    def write_rows(path, rows):
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def write_stats(path, stats):
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["indicateur", "valeur"])
            writer.writerows(stats.items())

    def save_graphs(self, folder):
        valid = [f for f in self.frames if f.rssi is not None]
        if valid:
            fig, ax = plt.subplots()
            t0 = valid[0].timestamp
            ax.plot([f.timestamp - t0 for f in valid], [f.rssi for f in valid])
            ax.set_xlabel("Temps (s)")
            ax.set_ylabel("RSSI (dBm)")
            ax.grid(True)
            fig.tight_layout()
            fig.savefig(folder / "rssi.png", dpi=160)
            plt.close(fig)

        intervals = [e.interval_ms for e in self.events if e.interval_ms is not None]
        if intervals:
            fig, ax = plt.subplots()
            ax.plot(range(1, len(intervals) + 1), intervals, marker="o")
            ax.set_ylabel("Intervalle (ms)")
            ax.grid(True)
            fig.tight_layout()
            fig.savefig(folder / "intervalles.png", dpi=160)
            plt.close(fig)

    def log(self, text):
        self.log_text.insert("end", str(text) + "\n")
        self.log_text.see("end")

    def close_app(self):
        if self.capture:
            try:
                self.capture.stop()
            except Exception:
                pass
        self.destroy()

if __name__ == "__main__":
    IBeaconV2App().mainloop()
