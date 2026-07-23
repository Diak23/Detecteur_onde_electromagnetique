#!/usr/bin/env python3
from __future__ import annotations

import csv
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Optional
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


APP_TITLE = "Plateforme iBeacon V5 — acquisition contrôlée"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "acquisitions_ibeacon_v5"

DEFAULT_INTERFACE = "/dev/ttyUSB0-4.4"
DEFAULT_GROUP_WINDOW_MS = 20.0
DEFAULT_PHY = "LE 1M"
DEFAULT_FALLBACK_AIRTIME_US = 376.0
DEFAULT_RSSI_OFFSET_DB = 0.0

IBEACON_BODY_HEX_LEN = 46
MAX_RAW_LINES = 1200

PDU_NAMES = {
    0: "ADV_IND",
    1: "ADV_DIRECT_IND",
    2: "ADV_NONCONN_IND",
    3: "SCAN_REQ",
    4: "SCAN_RSP",
    5: "CONNECT_IND",
    6: "ADV_SCAN_IND",
    7: "AUX/RESERVED",
}


@dataclass
class IBeaconFrame:
    timestamp: float
    address: str
    pdu_type_code: Optional[int]
    pdu_type_name: str
    length_bytes: Optional[int]
    rssi_dbm: Optional[float]
    channel: Optional[int]
    uuid: str
    major: int
    minor: int
    tx_power_dbm: int
    raw_hex: str
    source_field: str

    device_id: str = ""
    canonical_uuid: str = ""
    calibrated_rssi_dbm: Optional[float] = None
    received_power_w: Optional[float] = None
    received_power_nw: Optional[float] = None
    airtime_us: Optional[float] = None
    frame_energy_j: Optional[float] = None
    frame_energy_nj: Optional[float] = None

    def as_dict(self):
        return asdict(self)


@dataclass
class IBeaconEvent:
    index: int
    device_id: str
    canonical_uuid: str
    address: str
    start_epoch: float
    end_epoch: float
    span_ms: float
    interval_ms: Optional[float]
    packet_count: int
    channels: str
    pdu_types: str
    length_mean_bytes: Optional[float]
    rssi_mean_dbm: Optional[float]
    total_airtime_us: float
    event_energy_j: float
    event_energy_nj: float
    cumulative_energy_j: float
    cumulative_energy_nj: float

    def as_dict(self):
        return asdict(self)


class LogicalDeviceResolver:
    """
    Même UUID OU même adresse MAC => même appareil logique.
    Utilise une union-find afin de fusionner les groupes déjà créés.
    """
    def __init__(self):
        self.parent = {}
        self.rank = {}
        self.uuid_owner = {}
        self.mac_owner = {}
        self.next_id = 1
        self.uuid_counts = defaultdict(Counter)
        self.mac_counts = defaultdict(Counter)

    def _create_device(self):
        device = f"Appareil_{self.next_id:03d}"
        self.next_id += 1
        self.parent[device] = device
        self.rank[device] = 0
        return device

    def find(self, device):
        if self.parent[device] != device:
            self.parent[device] = self.find(self.parent[device])
        return self.parent[device]

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra

        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra

        self.parent[rb] = ra

        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1

        self.uuid_counts[ra].update(self.uuid_counts.pop(rb, Counter()))
        self.mac_counts[ra].update(self.mac_counts.pop(rb, Counter()))

        for uuid, owner in list(self.uuid_owner.items()):
            if self.find(owner) == rb:
                self.uuid_owner[uuid] = ra

        for mac, owner in list(self.mac_owner.items()):
            if self.find(owner) == rb:
                self.mac_owner[mac] = ra

        return ra

    def assign(self, uuid, mac):
        uuid = (uuid or "").lower()
        mac = (mac or "").lower()

        candidates = []

        if uuid in self.uuid_owner:
            candidates.append(self.find(self.uuid_owner[uuid]))

        if mac and mac in self.mac_owner:
            candidates.append(self.find(self.mac_owner[mac]))

        if not candidates:
            root = self._create_device()
        else:
            root = candidates[0]
            for other in candidates[1:]:
                root = self.union(root, other)

        self.uuid_owner[uuid] = root
        if mac:
            self.mac_owner[mac] = root

        self.uuid_counts[root][uuid] += 1
        if mac:
            self.mac_counts[root][mac] += 1

        return self.find(root)

    def canonical_uuid(self, device):
        root = self.find(device)
        counts = self.uuid_counts[root]
        return counts.most_common(1)[0][0] if counts else ""

    def canonical_mac(self, device):
        root = self.find(device)
        counts = self.mac_counts[root]
        return counts.most_common(1)[0][0] if counts else ""

    def normalize_frames(self, frames):
        for frame in frames:
            frame.device_id = self.find(frame.device_id)
            frame.canonical_uuid = self.canonical_uuid(frame.device_id)


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

    start = 0
    while True:
        pos = h.find("0215", start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 2

    for pos in dict.fromkeys(positions):
        body = h[pos:pos + IBEACON_BODY_HEX_LEN]

        if len(body) != IBEACON_BODY_HEX_LEN or not body.startswith("0215"):
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
            "tx_power_dbm": signed8(raw[22]),
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
        text = str(value).strip()
        if not text:
            return None
        return int(text, 0)
    except Exception:
        try:
            return int(float(str(value)))
        except Exception:
            return None


def dbm_to_watts(dbm):
    return 10 ** ((dbm - 30.0) / 10.0)


def estimate_airtime_us(length_bytes, phy, fallback_airtime_us):
    if length_bytes is None or length_bytes < 0:
        return fallback_airtime_us

    if phy == "LE 1M":
        return (length_bytes + 10) * 8.0

    if phy == "LE 2M":
        return (length_bytes + 11) * 4.0

    return fallback_airtime_us


def compute_frame_energy(frame, phy, fallback_airtime_us, rssi_offset_db):
    frame.airtime_us = estimate_airtime_us(
        frame.length_bytes,
        phy,
        fallback_airtime_us,
    )

    if frame.rssi_dbm is None:
        return

    frame.calibrated_rssi_dbm = frame.rssi_dbm + rssi_offset_db
    frame.received_power_w = dbm_to_watts(frame.calibrated_rssi_dbm)
    frame.received_power_nw = frame.received_power_w * 1e9
    frame.frame_energy_j = frame.received_power_w * frame.airtime_us * 1e-6
    frame.frame_energy_nj = frame.frame_energy_j * 1e9


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
            field for field in self.METADATA_FIELDS if field in available
        ]
        self.raw_fields = [
            field for field in self.RAW_FIELD_CANDIDATES if field in available
        ]

        if "frame.time_epoch" not in self.metadata_fields:
            raise RuntimeError("Le champ frame.time_epoch est indisponible.")

        if not self.raw_fields:
            raise RuntimeError("Aucun champ brut BLE compatible trouvé.")

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
        if shutil.which("tshark") is None:
            raise RuntimeError("tshark est introuvable.")

        command = self.build_command()
        self.on_log("Commande : " + " ".join(command))

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
        fields = self.metadata_fields + self.raw_fields
        index = {field: i for i, field in enumerate(fields)}

        assert self.process is not None
        assert self.process.stdout is not None

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

                for candidate in [v for v in value.split("|") if v.strip()]:
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

            pdu_code = (
                parse_int(parts[index["btle.advertising_header.pdu_type"]])
                if "btle.advertising_header.pdu_type" in index
                else None
            )

            frame = IBeaconFrame(
                timestamp=timestamp,
                address=(
                    parts[index["btle.advertising_address"]]
                    if "btle.advertising_address" in index else ""
                ),
                pdu_type_code=pdu_code,
                pdu_type_name=PDU_NAMES.get(
                    pdu_code,
                    f"INCONNU_{pdu_code}" if pdu_code is not None else "INCONNU",
                ),
                length_bytes=(
                    parse_int(parts[index["btle.length"]])
                    if "btle.length" in index else None
                ),
                rssi_dbm=(
                    parse_float(parts[index["nordic_ble.rssi"]])
                    if "nordic_ble.rssi" in index else None
                ),
                channel=(
                    parse_int(parts[index["nordic_ble.channel"]])
                    if "nordic_ble.channel" in index else None
                ),
                uuid=decoded["uuid"],
                major=decoded["major"],
                minor=decoded["minor"],
                tx_power_dbm=decoded["tx_power_dbm"],
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
        if not groups:
            groups.append([frame])
            continue

        previous = groups[-1][-1]
        same_device = frame.device_id == previous.device_id
        gap_ms = (frame.timestamp - previous.timestamp) * 1000.0

        if same_device and gap_ms <= window_ms:
            groups[-1].append(frame)
        else:
            groups.append([frame])

    previous_start = {}
    cumulative = defaultdict(float)
    events = []

    for index, group in enumerate(groups, start=1):
        first = group[0]
        last = group[-1]

        interval_ms = None
        if first.device_id in previous_start:
            interval_ms = (
                first.timestamp - previous_start[first.device_id]
            ) * 1000.0

        previous_start[first.device_id] = first.timestamp

        lengths = [
            frame.length_bytes for frame in group
            if frame.length_bytes is not None
        ]
        rssis = [
            frame.calibrated_rssi_dbm for frame in group
            if frame.calibrated_rssi_dbm is not None
        ]
        event_energy_j = sum(frame.frame_energy_j or 0.0 for frame in group)
        cumulative[first.device_id] += event_energy_j

        events.append(
            IBeaconEvent(
                index=index,
                device_id=first.device_id,
                canonical_uuid=first.canonical_uuid,
                address=first.address,
                start_epoch=first.timestamp,
                end_epoch=last.timestamp,
                span_ms=(last.timestamp - first.timestamp) * 1000.0,
                interval_ms=interval_ms,
                packet_count=len(group),
                channels=",".join(
                    str(v) for v in sorted({
                        frame.channel for frame in group
                        if frame.channel is not None
                    })
                ),
                pdu_types=",".join(sorted({
                    frame.pdu_type_name for frame in group
                })),
                length_mean_bytes=mean(lengths) if lengths else None,
                rssi_mean_dbm=mean(rssis) if rssis else None,
                total_airtime_us=sum(frame.airtime_us or 0.0 for frame in group),
                event_energy_j=event_energy_j,
                event_energy_nj=event_energy_j * 1e9,
                cumulative_energy_j=cumulative[first.device_id],
                cumulative_energy_nj=cumulative[first.device_id] * 1e9,
            )
        )

    return events


def stats_by_device(frames, events):
    frames_by_device = defaultdict(list)
    events_by_device = defaultdict(list)

    for frame in frames:
        frames_by_device[frame.device_id].append(frame)

    for event in events:
        events_by_device[event.device_id].append(event)

    rows = []

    for device_id in sorted(frames_by_device):
        device_frames = frames_by_device[device_id]
        device_events = events_by_device.get(device_id, [])

        rssis = [
            f.calibrated_rssi_dbm for f in device_frames
            if f.calibrated_rssi_dbm is not None
        ]
        powers = [
            f.received_power_w for f in device_frames
            if f.received_power_w is not None
        ]
        energies = [
            f.frame_energy_j for f in device_frames
            if f.frame_energy_j is not None
        ]
        lengths = [
            f.length_bytes for f in device_frames
            if f.length_bytes is not None
        ]
        intervals = [
            e.interval_ms for e in device_events
            if e.interval_ms is not None
        ]

        uuid_counts = Counter(f.uuid for f in device_frames)
        mac_counts = Counter(f.address for f in device_frames if f.address)

        rows.append({
            "device_id": device_id,
            "uuid_principal": uuid_counts.most_common(1)[0][0] if uuid_counts else "",
            "adresse_mac_principale": mac_counts.most_common(1)[0][0] if mac_counts else "",
            "uuid_observes": " | ".join(sorted(uuid_counts)),
            "adresses_mac_observees": " | ".join(sorted(mac_counts)),
            "nombre_trames": len(device_frames),
            "nombre_evenements": len(device_events),
            "rssi_moyen_dbm": mean(rssis) if rssis else None,
            "rssi_min_dbm": min(rssis) if rssis else None,
            "rssi_max_dbm": max(rssis) if rssis else None,
            "puissance_moyenne_nw": mean(powers) * 1e9 if powers else None,
            "energie_totale_nj": sum(energies) * 1e9,
            "longueur_moyenne_octets": mean(lengths) if lengths else None,
            "longueur_min_octets": min(lengths) if lengths else None,
            "longueur_max_octets": max(lengths) if lengths else None,
            "intervalle_moyen_ms": mean(intervals) if intervals else None,
            "canal_37": sum(f.channel == 37 for f in device_frames),
            "canal_38": sum(f.channel == 38 for f in device_frames),
            "canal_39": sum(f.channel == 39 for f in device_frames),
        })

    return rows


class IBeaconApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1550x950")
        self.minsize(1250, 800)

        self.frames = []
        self.events = []
        self.capture = None
        self.resolver = LogicalDeviceResolver()
        self.event_queue = queue.Queue()

        self.capture_started_monotonic = None
        self.capture_started_datetime = None
        self.stop_reason = ""
        self.auto_stop_requested = False

        self.interface_var = tk.StringVar(value=DEFAULT_INTERFACE)
        self.window_var = tk.StringVar(value=str(DEFAULT_GROUP_WINDOW_MS))
        self.phy_var = tk.StringVar(value=DEFAULT_PHY)
        self.fallback_airtime_var = tk.StringVar(value=str(DEFAULT_FALLBACK_AIRTIME_US))
        self.rssi_offset_var = tk.StringVar(value=str(DEFAULT_RSSI_OFFSET_DB))

        self.mode_var = tk.StringVar(value="Acquisition complète")
        self.duration_choice_var = tk.StringVar(value="30 secondes")
        self.custom_duration_var = tk.StringVar(value="30")
        self.frame_limit_choice_var = tk.StringVar(value="100 trames")
        self.custom_frame_limit_var = tk.StringVar(value="100")

        self.status_var = tk.StringVar(value="Capture arrêtée")
        self.progress_text_var = tk.StringVar(value="Prêt")
        self.frame_count_var = tk.StringVar(value="Trames : 0")
        self.device_count_var = tk.StringVar(value="Appareils : 0")
        self.energy_var = tk.StringVar(value="Énergie : 0 nJ")

        self.graph_var = tk.StringVar(value="RSSI par appareil")

        self._build_interface()
        self.refresh_interfaces()

        self.after(100, self._process_queue)
        self.after(200, self._update_progress)
        self.protocol("WM_DELETE_WINDOW", self.close_application)

    def _build_interface(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=10)

        ttk.Label(
            header,
            text=APP_TITLE,
            font=("TkDefaultFont", 16, "bold"),
        ).pack(side="left")

        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=8)

        self.acquisition_tab = ttk.Frame(notebook)
        self.frames_tab = ttk.Frame(notebook)
        self.events_tab = ttk.Frame(notebook)
        self.devices_tab = ttk.Frame(notebook)
        self.graph_tab = ttk.Frame(notebook)
        self.raw_tab = ttk.Frame(notebook)
        self.log_tab = ttk.Frame(notebook)

        notebook.add(self.acquisition_tab, text="Configuration acquisition")
        notebook.add(self.frames_tab, text="Trames")
        notebook.add(self.events_tab, text="Événements")
        notebook.add(self.devices_tab, text="Appareils")
        notebook.add(self.graph_tab, text="Graphiques")
        notebook.add(self.raw_tab, text="Diagnostic brut")
        notebook.add(self.log_tab, text="Journal")

        self._build_acquisition_tab()
        self._build_frames_tab()
        self._build_events_tab()
        self._build_devices_tab()
        self._build_graph_tab()
        self._build_raw_tab()
        self._build_log_tab()

    def _build_acquisition_tab(self):
        config = ttk.LabelFrame(self.acquisition_tab, text="Capture et énergie")
        config.pack(fill="x", padx=12, pady=12)
        config.columnconfigure(1, weight=1)

        rows = [
            ("Interface nRF Sniffer", self.interface_var),
            ("Fenêtre de regroupement (ms)", self.window_var),
            ("PHY BLE", self.phy_var),
            ("Durée de repli (µs)", self.fallback_airtime_var),
            ("Correction RSSI (dB)", self.rssi_offset_var),
        ]

        for row, (label, variable) in enumerate(rows):
            ttk.Label(config, text=label).grid(row=row, column=0, padx=8, pady=5)

            if label == "Interface nRF Sniffer":
                widget = ttk.Combobox(config, textvariable=variable)
                self.interface_combo = widget
            elif label == "PHY BLE":
                widget = ttk.Combobox(
                    config,
                    textvariable=variable,
                    values=("LE 1M", "LE 2M", "LE Coded"),
                    state="readonly",
                )
            else:
                widget = ttk.Entry(config, textvariable=variable)

            widget.grid(row=row, column=1, sticky="ew", padx=8, pady=5)

        ttk.Button(
            config,
            text="Actualiser interfaces",
            command=self.refresh_interfaces,
        ).grid(row=0, column=2, padx=8)

        mode_frame = ttk.LabelFrame(
            self.acquisition_tab,
            text="Mode d'acquisition",
        )
        mode_frame.pack(fill="x", padx=12, pady=8)
        mode_frame.columnconfigure(1, weight=1)

        ttk.Label(mode_frame, text="Mode").grid(row=0, column=0, padx=8, pady=6)
        ttk.Combobox(
            mode_frame,
            textvariable=self.mode_var,
            values=(
                "Acquisition complète",
                "Durée limitée",
                "Nombre de trames",
            ),
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(mode_frame, text="Durée prédéfinie").grid(
            row=1, column=0, padx=8, pady=6
        )
        ttk.Combobox(
            mode_frame,
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

        ttk.Label(mode_frame, text="Durée personnalisée (s)").grid(
            row=2, column=0, padx=8, pady=6
        )
        ttk.Entry(
            mode_frame,
            textvariable=self.custom_duration_var,
        ).grid(row=2, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(mode_frame, text="Nombre de trames").grid(
            row=3, column=0, padx=8, pady=6
        )
        ttk.Combobox(
            mode_frame,
            textvariable=self.frame_limit_choice_var,
            values=(
                "100 trames",
                "500 trames",
                "1000 trames",
                "Nombre personnalisé",
            ),
            state="readonly",
        ).grid(row=3, column=1, sticky="ew", padx=8, pady=6)

        ttk.Label(mode_frame, text="Nombre personnalisé").grid(
            row=4, column=0, padx=8, pady=6
        )
        ttk.Entry(
            mode_frame,
            textvariable=self.custom_frame_limit_var,
        ).grid(row=4, column=1, sticky="ew", padx=8, pady=6)

        buttons = ttk.Frame(self.acquisition_tab)
        buttons.pack(fill="x", padx=12, pady=10)

        ttk.Button(buttons, text="Démarrer", command=self.start_capture).pack(
            side="left", padx=5
        )
        ttk.Button(
            buttons,
            text="Arrêter et sauvegarder",
            command=lambda: self.stop_capture("arrêt manuel"),
        ).pack(side="left", padx=5)
        ttk.Button(buttons, text="Effacer", command=self.clear_data).pack(
            side="left", padx=5
        )

        progress_frame = ttk.LabelFrame(
            self.acquisition_tab,
            text="Progression",
        )
        progress_frame.pack(fill="x", padx=12, pady=8)

        self.progress_bar = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.pack(fill="x", padx=12, pady=10)

        ttk.Label(
            progress_frame,
            textvariable=self.progress_text_var,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", padx=12, pady=6)

        counters = ttk.Frame(progress_frame)
        counters.pack(fill="x", padx=12, pady=8)

        for variable in (
            self.frame_count_var,
            self.device_count_var,
            self.energy_var,
        ):
            ttk.Label(counters, textvariable=variable).pack(
                side="left", padx=12
            )

    def _build_frames_tab(self):
        columns = (
            "time", "device", "mac", "uuid", "major", "minor",
            "rssi", "length", "pdu_code", "pdu_name",
            "channel", "airtime", "power", "energy",
        )

        self.frame_table = ttk.Treeview(
            self.frames_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "time": "Temps",
            "device": "Appareil",
            "mac": "Adresse MAC",
            "uuid": "UUID",
            "major": "Major",
            "minor": "Minor",
            "rssi": "RSSI (dBm)",
            "length": "Longueur (octets)",
            "pdu_code": "PDU code",
            "pdu_name": "Type PDU",
            "channel": "Canal",
            "airtime": "Durée RF (µs)",
            "power": "Puissance (nW)",
            "energy": "Énergie (nJ)",
        }

        for column in columns:
            self.frame_table.heading(column, text=headings[column])
            self.frame_table.column(
                column,
                width=290 if column == "uuid" else 115,
                anchor="center",
            )

        self.frame_table.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_events_tab(self):
        columns = (
            "index", "device", "uuid", "mac", "span", "interval",
            "packets", "length", "pdu", "channels", "airtime", "energy",
        )

        self.event_table = ttk.Treeview(
            self.events_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "index": "N°",
            "device": "Appareil",
            "uuid": "UUID principal",
            "mac": "MAC",
            "span": "Étendue (ms)",
            "interval": "Intervalle (ms)",
            "packets": "Paquets",
            "length": "Longueur moyenne",
            "pdu": "PDU",
            "channels": "Canaux",
            "airtime": "Durée RF totale",
            "energy": "Énergie événement",
        }

        for column in columns:
            self.event_table.heading(column, text=headings[column])
            self.event_table.column(
                column,
                width=280 if column == "uuid" else 120,
                anchor="center",
            )

        self.event_table.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_devices_tab(self):
        columns = (
            "device", "uuid", "mac", "frames", "events",
            "rssi", "length_mean", "length_min", "length_max",
            "power", "energy", "interval",
        )

        self.device_table = ttk.Treeview(
            self.devices_tab,
            columns=columns,
            show="headings",
        )

        headings = {
            "device": "Appareil",
            "uuid": "UUID principal",
            "mac": "MAC principale",
            "frames": "Trames",
            "events": "Événements",
            "rssi": "RSSI moyen",
            "length_mean": "Longueur moyenne",
            "length_min": "Longueur min",
            "length_max": "Longueur max",
            "power": "Puissance moyenne",
            "energy": "Énergie totale",
            "interval": "Intervalle moyen",
        }

        for column in columns:
            self.device_table.heading(column, text=headings[column])
            self.device_table.column(
                column,
                width=290 if column == "uuid" else 125,
                anchor="center",
            )

        self.device_table.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_graph_tab(self):
        controls = ttk.Frame(self.graph_tab)
        controls.pack(fill="x", padx=12, pady=8)

        ttk.Combobox(
            controls,
            textvariable=self.graph_var,
            values=(
                "RSSI par appareil",
                "Puissance par appareil",
                "Énergie par trame",
                "Énergie cumulée par appareil",
                "Intervalles par appareil",
                "Longueur des paquets",
                "Répartition des PDU",
                "Répartition des canaux",
            ),
            state="readonly",
            width=32,
        ).pack(side="left", padx=8)

        ttk.Button(
            controls,
            text="Actualiser",
            command=self.draw_graph,
        ).pack(side="left")

        self.figure = Figure(figsize=(12, 7), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.graph_tab)
        self.canvas.get_tk_widget().pack(
            fill="both", expand=True, padx=12, pady=12
        )

    def _build_raw_tab(self):
        self.raw_text = tk.Text(self.raw_tab, wrap="none")
        self.raw_text.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_log_tab(self):
        self.log_text = tk.Text(self.log_tab)
        self.log_text.pack(fill="both", expand=True, padx=12, pady=12)

    def refresh_interfaces(self):
        try:
            interfaces = NRFCapture.list_interfaces()
            self.interface_combo["values"] = interfaces

            matches = [
                value for value in interfaces
                if "nrf" in value.lower() or "ttyusb" in value.lower()
            ]
            if matches:
                self.interface_var.set(matches[0].split(" (", 1)[0])

            self.log("Interfaces actualisées.")
        except Exception as exc:
            self.log(f"Erreur interface : {exc}")

    def selected_duration_seconds(self):
        mapping = {
            "30 secondes": 30,
            "1 minute": 60,
            "2 minutes": 120,
            "5 minutes": 300,
        }

        choice = self.duration_choice_var.get()
        if choice in mapping:
            return mapping[choice]

        value = float(self.custom_duration_var.get().replace(",", "."))
        if value <= 0:
            raise ValueError("La durée doit être positive.")
        return value

    def selected_frame_limit(self):
        mapping = {
            "100 trames": 100,
            "500 trames": 500,
            "1000 trames": 1000,
        }

        choice = self.frame_limit_choice_var.get()
        if choice in mapping:
            return mapping[choice]

        value = int(self.custom_frame_limit_var.get())
        if value <= 0:
            raise ValueError("Le nombre de trames doit être positif.")
        return value

    def start_capture(self):
        if self.capture is not None:
            messagebox.showwarning("Capture", "Une capture est déjà active.")
            return

        try:
            float(self.window_var.get().replace(",", "."))
            float(self.fallback_airtime_var.get().replace(",", "."))
            float(self.rssi_offset_var.get().replace(",", "."))

            if self.mode_var.get() == "Durée limitée":
                self.selected_duration_seconds()
            elif self.mode_var.get() == "Nombre de trames":
                self.selected_frame_limit()

            self.auto_stop_requested = False
            self.stop_reason = ""
            self.capture_started_monotonic = time.monotonic()
            self.capture_started_datetime = datetime.now()

            self.capture = NRFCapture(
                self.interface_var.get().strip(),
                lambda frame: self.event_queue.put(("frame", frame)),
                lambda msg: self.event_queue.put(("log", msg)),
                lambda msg: self.event_queue.put(("raw", msg)),
            )
            self.capture.start()

            self.status_var.set("Capture active")
            self.log("Capture démarrée.")

        except Exception as exc:
            self.capture = None
            messagebox.showerror("Erreur", str(exc))

    def stop_capture(self, reason):
        if self.capture is not None:
            self.capture.stop()
            self.capture = None

        self.stop_reason = reason
        self.status_var.set("Capture arrêtée")
        self.analyse()

        if self.frames:
            self.export_results()
        else:
            messagebox.showwarning("Capture", "Aucune trame iBeacon détectée.")

    def clear_data(self):
        if self.capture is not None:
            messagebox.showwarning("Capture", "Arrêtez d'abord la capture.")
            return

        self.frames.clear()
        self.events.clear()
        self.resolver = LogicalDeviceResolver()
        self.progress_bar["value"] = 0
        self.progress_text_var.set("Prêt")
        self.frame_count_var.set("Trames : 0")
        self.device_count_var.set("Appareils : 0")
        self.energy_var.set("Énergie : 0 nJ")

        for table in (self.frame_table, self.event_table, self.device_table):
            for item in table.get_children():
                table.delete(item)

        self.axis.clear()
        self.canvas.draw_idle()
        self.raw_text.delete("1.0", "end")

    def _process_queue(self):
        while True:
            try:
                event_type, value = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "frame":
                self.receive_frame(value)
            elif event_type == "log":
                self.log(value)
            elif event_type == "raw":
                if int(self.raw_text.index("end-1c").split(".")[0]) < MAX_RAW_LINES:
                    self.raw_text.insert("end", value + "\n")
                    self.raw_text.see("end")

        self.after(100, self._process_queue)

    def receive_frame(self, frame):
        frame.device_id = self.resolver.assign(frame.uuid, frame.address)
        frame.canonical_uuid = self.resolver.canonical_uuid(frame.device_id)

        compute_frame_energy(
            frame,
            self.phy_var.get(),
            float(self.fallback_airtime_var.get().replace(",", ".")),
            float(self.rssi_offset_var.get().replace(",", ".")),
        )

        self.frames.append(frame)
        self.resolver.normalize_frames(self.frames)

        self.frame_count_var.set(f"Trames : {len(self.frames)}")
        self.device_count_var.set(
            f"Appareils : {len({f.device_id for f in self.frames})}"
        )
        self.energy_var.set(
            f"Énergie : {sum(f.frame_energy_nj or 0.0 for f in self.frames):.8f} nJ"
        )

        self.frame_table.insert(
            "",
            "end",
            values=(
                f"{frame.timestamp:.6f}",
                frame.device_id,
                frame.address,
                frame.uuid,
                frame.major,
                frame.minor,
                self.fmt(frame.calibrated_rssi_dbm),
                "" if frame.length_bytes is None else frame.length_bytes,
                "" if frame.pdu_type_code is None else frame.pdu_type_code,
                frame.pdu_type_name,
                "" if frame.channel is None else frame.channel,
                self.fmt(frame.airtime_us),
                self.fmt(frame.received_power_nw, 8),
                self.fmt(frame.frame_energy_nj, 10),
            ),
        )

        if self.mode_var.get() == "Nombre de trames":
            limit = self.selected_frame_limit()
            if len(self.frames) >= limit and not self.auto_stop_requested:
                self.auto_stop_requested = True
                self.after(10, lambda: self.stop_capture("limite de trames atteinte"))

    def _update_progress(self):
        if self.capture is not None and self.capture_started_monotonic is not None:
            elapsed = time.monotonic() - self.capture_started_monotonic
            mode = self.mode_var.get()

            if mode == "Durée limitée":
                target = self.selected_duration_seconds()
                remaining = max(0.0, target - elapsed)
                percent = min(100.0, elapsed / target * 100.0)
                self.progress_bar["value"] = percent
                self.progress_text_var.set(
                    f"Temps écoulé : {elapsed:.1f} s | restant : {remaining:.1f} s | "
                    f"progression : {percent:.1f} %"
                )

                if elapsed >= target and not self.auto_stop_requested:
                    self.auto_stop_requested = True
                    self.after(10, lambda: self.stop_capture("durée atteinte"))

            elif mode == "Nombre de trames":
                target = self.selected_frame_limit()
                count = len(self.frames)
                percent = min(100.0, count / target * 100.0)
                self.progress_bar["value"] = percent
                self.progress_text_var.set(
                    f"Trames reçues : {count}/{target} | restantes : "
                    f"{max(0, target-count)} | progression : {percent:.1f} %"
                )

            else:
                self.progress_bar["value"] = 0
                self.progress_text_var.set(
                    f"Acquisition complète | temps écoulé : {elapsed:.1f} s | "
                    f"trames : {len(self.frames)}"
                )

        self.after(200, self._update_progress)

    def analyse(self):
        self.resolver.normalize_frames(self.frames)
        self.events = group_events(
            self.frames,
            float(self.window_var.get().replace(",", ".")),
        )

        for table in (self.event_table, self.device_table):
            for item in table.get_children():
                table.delete(item)

        for event in self.events:
            self.event_table.insert(
                "",
                "end",
                values=(
                    event.index,
                    event.device_id,
                    event.canonical_uuid,
                    event.address,
                    self.fmt(event.span_ms),
                    self.fmt(event.interval_ms),
                    event.packet_count,
                    self.fmt(event.length_mean_bytes),
                    event.pdu_types,
                    event.channels,
                    self.fmt(event.total_airtime_us),
                    self.fmt(event.event_energy_nj, 10),
                ),
            )

        for row in stats_by_device(self.frames, self.events):
            self.device_table.insert(
                "",
                "end",
                values=(
                    row["device_id"],
                    row["uuid_principal"],
                    row["adresse_mac_principale"],
                    row["nombre_trames"],
                    row["nombre_evenements"],
                    self.fmt(row["rssi_moyen_dbm"]),
                    self.fmt(row["longueur_moyenne_octets"]),
                    self.fmt(row["longueur_min_octets"]),
                    self.fmt(row["longueur_max_octets"]),
                    self.fmt(row["puissance_moyenne_nw"], 8),
                    self.fmt(row["energie_totale_nj"], 10),
                    self.fmt(row["intervalle_moyen_ms"]),
                ),
            )

        self.draw_graph()

    def draw_graph(self):
        self.axis.clear()
        name = self.graph_var.get()

        grouped = defaultdict(list)
        for frame in self.frames:
            grouped[frame.device_id].append(frame)

        labels = {
            device: f"{device} | {frames[0].canonical_uuid[:8]}…"
            for device, frames in grouped.items()
        }

        if name == "RSSI par appareil":
            if grouped:
                start = min(f.timestamp for fs in grouped.values() for f in fs)
                for device in sorted(grouped):
                    values = sorted(grouped[device], key=lambda f: f.timestamp)
                    self.axis.plot(
                        [f.timestamp - start for f in values],
                        [f.calibrated_rssi_dbm for f in values],
                        marker="o",
                        markersize=3,
                        linewidth=1,
                        label=labels[device],
                    )
                self.axis.set_xlabel("Temps (s)")
                self.axis.set_ylabel("RSSI (dBm)")

        elif name == "Puissance par appareil":
            if grouped:
                start = min(f.timestamp for fs in grouped.values() for f in fs)
                for device in sorted(grouped):
                    values = [f for f in grouped[device] if f.received_power_nw is not None]
                    self.axis.plot(
                        [f.timestamp - start for f in values],
                        [f.received_power_nw for f in values],
                        marker="o",
                        markersize=3,
                        linewidth=1,
                        label=labels[device],
                    )
                self.axis.set_xlabel("Temps (s)")
                self.axis.set_ylabel("Puissance (nW)")

        elif name == "Énergie par trame":
            for device in sorted(grouped):
                values = [f for f in grouped[device] if f.frame_energy_nj is not None]
                self.axis.plot(
                    range(1, len(values) + 1),
                    [f.frame_energy_nj for f in values],
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=labels[device],
                )
            self.axis.set_xlabel("Numéro de trame")
            self.axis.set_ylabel("Énergie (nJ)")

        elif name == "Énergie cumulée par appareil":
            events_by_device = defaultdict(list)
            for event in self.events:
                events_by_device[event.device_id].append(event)

            for device in sorted(events_by_device):
                values = events_by_device[device]
                self.axis.plot(
                    range(1, len(values) + 1),
                    [e.cumulative_energy_nj for e in values],
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=labels.get(device, device),
                )
            self.axis.set_xlabel("Numéro d'événement")
            self.axis.set_ylabel("Énergie cumulée (nJ)")

        elif name == "Intervalles par appareil":
            events_by_device = defaultdict(list)
            for event in self.events:
                if event.interval_ms is not None:
                    events_by_device[event.device_id].append(event)

            for device in sorted(events_by_device):
                values = events_by_device[device]
                self.axis.plot(
                    range(1, len(values) + 1),
                    [e.interval_ms for e in values],
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=labels.get(device, device),
                )
            self.axis.set_xlabel("Numéro d'événement")
            self.axis.set_ylabel("Intervalle (ms)")

        elif name == "Longueur des paquets":
            for device in sorted(grouped):
                values = [f for f in grouped[device] if f.length_bytes is not None]
                self.axis.plot(
                    range(1, len(values) + 1),
                    [f.length_bytes for f in values],
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=labels[device],
                )
            self.axis.set_xlabel("Numéro de trame")
            self.axis.set_ylabel("Longueur (octets)")

        elif name == "Répartition des PDU":
            counts = Counter(f.pdu_type_name for f in self.frames)
            self.axis.bar(list(counts.keys()), list(counts.values()))
            self.axis.set_xlabel("Type de PDU")
            self.axis.set_ylabel("Nombre de trames")
            self.axis.tick_params(axis="x", rotation=45)

        elif name == "Répartition des canaux":
            counts = Counter(f.channel for f in self.frames if f.channel is not None)
            channels = sorted(counts)
            self.axis.bar([str(c) for c in channels], [counts[c] for c in channels])
            self.axis.set_xlabel("Canal BLE")
            self.axis.set_ylabel("Nombre de trames")

        self.axis.set_title(name)
        self.axis.grid(True)

        if name not in ("Répartition des PDU", "Répartition des canaux") and grouped:
            self.axis.legend(loc="best", fontsize=8)

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def export_results(self):
        folder = OUTPUT_DIR / (
            "acquisition_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        graph_folder = folder / "graphes"
        graph_folder.mkdir(parents=True, exist_ok=True)

        self.write_rows(
            folder / "trames_energie.csv",
            [f.as_dict() for f in self.frames],
        )
        self.write_rows(
            folder / "evenements_energie.csv",
            [e.as_dict() for e in self.events],
        )

        device_rows = stats_by_device(self.frames, self.events)
        self.write_rows(folder / "appareils_ble.csv", device_rows)
        self.write_rows(folder / "energie_par_appareil.csv", device_rows)

        length_rows = []
        for device in sorted({f.device_id for f in self.frames}):
            values = [
                f.length_bytes for f in self.frames
                if f.device_id == device and f.length_bytes is not None
            ]
            length_rows.append({
                "device_id": device,
                "nombre_valeurs": len(values),
                "longueur_moyenne_octets": mean(values) if values else None,
                "longueur_mediane_octets": median(values) if values else None,
                "longueur_min_octets": min(values) if values else None,
                "longueur_max_octets": max(values) if values else None,
                "ecart_type_octets": pstdev(values) if len(values) > 1 else 0.0,
            })
        self.write_rows(folder / "statistiques_longueur.csv", length_rows)

        pdu_counts = Counter(f.pdu_type_name for f in self.frames)
        self.write_rows(
            folder / "statistiques_pdu.csv",
            [{"type_pdu": key, "nombre_trames": value}
             for key, value in sorted(pdu_counts.items())],
        )

        elapsed = (
            time.monotonic() - self.capture_started_monotonic
            if self.capture_started_monotonic is not None else None
        )

        params = {
            "mode_acquisition": self.mode_var.get(),
            "duree_demandee_s": (
                self.selected_duration_seconds()
                if self.mode_var.get() == "Durée limitée" else ""
            ),
            "nombre_trames_demande": (
                self.selected_frame_limit()
                if self.mode_var.get() == "Nombre de trames" else ""
            ),
            "heure_debut": (
                self.capture_started_datetime.isoformat()
                if self.capture_started_datetime else ""
            ),
            "heure_fin": datetime.now().isoformat(),
            "duree_reelle_s": elapsed,
            "nombre_trames_final": len(self.frames),
            "nombre_appareils_logiques": len({f.device_id for f in self.frames}),
            "motif_arret": self.stop_reason,
            "regle_regroupement": "meme UUID OU meme adresse MAC",
        }
        self.write_key_values(folder / "parametres_acquisition.csv", params)

        graphs = [
            ("RSSI par appareil", "rssi_par_appareil.png"),
            ("Puissance par appareil", "puissance_par_appareil.png"),
            ("Énergie par trame", "energie_par_trame.png"),
            ("Énergie cumulée par appareil", "energie_cumulee_par_appareil.png"),
            ("Intervalles par appareil", "intervalles_par_appareil.png"),
            ("Longueur des paquets", "longueur_paquets.png"),
            ("Répartition des PDU", "repartition_pdu.png"),
            ("Répartition des canaux", "repartition_canaux.png"),
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

    @staticmethod
    def write_rows(path, rows):
        if not rows:
            path.write_text("", encoding="utf-8")
            return

        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=list(rows[0].keys()),
                delimiter=";",
            )
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def write_key_values(path, values):
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["parametre", "valeur"])
            writer.writerows(values.items())

    @staticmethod
    def fmt(value, digits=3):
        if value is None:
            return ""
        return f"{value:.{digits}f}"

    def log(self, message):
        self.log_text.insert("end", str(message) + "\n")
        self.log_text.see("end")

    def close_application(self):
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    IBeaconApp().mainloop()
