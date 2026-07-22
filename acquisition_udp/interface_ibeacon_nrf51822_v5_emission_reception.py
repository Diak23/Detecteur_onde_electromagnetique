#from pathlib import Path
#import ast
#import textwrap

#dst = Path("/mnt/data/interface_ibeacon_nrf51822_v5_emission_reception.py")

#code = r'''#!/usr/bin/env python3
"""
Plateforme BLE iBeacon V5 : émission, capture, décodage, calibration et export.

Fonctions principales
---------------------
1. Émission iBeacon depuis le Bluetooth du Raspberry Pi :
   - UUID ;
   - Major ;
   - Minor ;
   - Tx Power calibré ;
   - intervalle d'advertising ;
   - nom local facultatif ;
   - données fabricant supplémentaires facultatives.

2. Capture avec le nRF Sniffer nRF51822 et tshark :
   - timestamp ;
   - adresse advertising ;
   - canal 37, 38 ou 39 ;
   - RSSI brut et calibré ;
   - longueur ;
   - type PDU ;
   - durée estimée ;
   - intervalle entre paquets ;
   - décodage UUID, Major, Minor et Tx Power.

3. Comparaison émission/réception :
   - UUID ;
   - Major ;
   - Minor ;
   - Tx Power ;
   - Company ID.

4. Export automatique à l'arrêt :
   - CSV complet ;
   - CSV brut ;
   - CSV calibré ;
   - CSV événements regroupés ;
   - CSV statistiques ;
   - paramètres JSON ;
   - calibration JSON ;
   - huit graphes PNG.

Prérequis
---------
sudo apt install -y tshark bluez python3-tk python3-matplotlib
sudo systemctl enable --now bluetooth

Lancement
---------
python3 interface_ibeacon_nrf51822_v5_emission_reception.py

Remarque
--------
La commande "manufacturer" du menu advertise de bluetoothctl doit être prise
en charge par la version de BlueZ installée. Le programme affiche le journal
BlueZ afin de faciliter le diagnostic.
"""

from __future__ import annotations

import csv
import json
import math
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# ===========================================================================
# Configuration générale
# ===========================================================================

APP_TITLE = "Plateforme BLE iBeacon V5 — émission, capture et analyse"
OUTPUT_ROOT = Path("acquisitions_ibeacon_v5")
CALIBRATION_FILE = Path("calibration_rssi.json")

REFRESH_MS = 400
MAX_POINTS = 400
DEFAULT_UUID = "e20a39f4-73f5-4bc4-a12f-17d1ad07a961"
DEFAULT_MAJOR = 0
DEFAULT_MINOR = 0
DEFAULT_TX_POWER = -56
DEFAULT_INTERVAL_MS = 100
DEFAULT_REFERENCE_RSSI = -56.0
DEFAULT_CALIBRATION_SAMPLES = 100

APPLE_COMPANY_ID = 0x004C
IBEACON_PREFIX = "0215"

FILTER_UUID = "UUID"
FILTER_UUID_MAJOR_MINOR = "UUID + Major + Minor"
FILTER_ALL_IBEACONS = "Tous les iBeacons"

TSHARK_FIELDS = [
    "frame.time_epoch",
    "btle.advertising_address",
    "btle.length",
    "btle.advertising_header.pdu_type",
    "nordic_ble.rssi",
    "nordic_ble.channel",
    "btcommon.eir_ad.entry.company_id",
    "btcommon.eir_ad.entry.data",
]


# ===========================================================================
# Fonctions utilitaires
# ===========================================================================

def normalize_hex(value: str) -> str:
    value = value.replace("0x", "").replace(":", "")
    value = value.replace("-", "").replace(" ", "")
    return re.sub(r"[^0-9a-fA-F]", "", value).lower()


def normalize_uuid(value: str) -> str:
    raw = normalize_hex(value)
    if len(raw) != 32:
        raise ValueError("L'UUID doit contenir exactement 16 octets.")
    return (
        f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-"
        f"{raw[16:20]}-{raw[20:32]}"
    )


def uuid_to_hex(value: str) -> str:
    return normalize_uuid(value).replace("-", "")


def signed_byte(value: int) -> int:
    if not -128 <= value <= 127:
        raise ValueError("Tx Power doit être compris entre -128 et 127 dBm.")
    return value & 0xFF


def decode_signed_byte(hex_byte: str) -> int:
    value = int(hex_byte, 16)
    return value - 256 if value >= 128 else value


def parse_int(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier.") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} doit être compris entre {minimum} et {maximum}.")
    return result


def safe_int(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        if value.lower().startswith("0x"):
            return int(value, 16)
        return int(float(value))
    except ValueError:
        return None


def duration_le1m_us(payload_length: int) -> float:
    """
    Estimation simplifiée de la durée radio d'un paquet BLE LE 1M :
    préambule 1 + Access Address 4 + en-tête 2 + payload + CRC 3 octets.
    """
    return float((10 + max(0, payload_length)) * 8)


def mean(values: list[float]) -> Optional[float]:
    return None if not values else sum(values) / len(values)


def std(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    return math.sqrt(
        sum((value - average) ** 2 for value in values) / (len(values) - 1)
    )


def robust_mean(values: list[float]) -> float:
    if not values:
        raise ValueError("Aucune valeur RSSI disponible.")
    ordered = sorted(values)
    trim = int(len(ordered) * 0.10)
    if trim > 0 and len(ordered) - 2 * trim >= 1:
        ordered = ordered[trim:-trim]
    return sum(ordered) / len(ordered)


def format_optional(value: Optional[float], digits: int = 2) -> str:
    return "" if value is None else f"{value:.{digits}f}"


# ===========================================================================
# Modèles de données
# ===========================================================================

@dataclass
class IBeaconConfig:
    uuid: str
    major: int
    minor: int
    tx_power_dbm: int
    interval_ms: int
    local_name: str = ""
    extra_manufacturer_hex: str = ""

    @property
    def payload_hex(self) -> str:
        base = (
            IBEACON_PREFIX
            + uuid_to_hex(self.uuid)
            + f"{self.major:04x}"
            + f"{self.minor:04x}"
            + f"{signed_byte(self.tx_power_dbm):02x}"
        )
        return base + normalize_hex(self.extra_manufacturer_hex)

    @property
    def payload_bytes(self) -> list[int]:
        return list(bytes.fromhex(self.payload_hex))


@dataclass
class DecodedIBeacon:
    company_id: int
    uuid: str
    major: int
    minor: int
    tx_power_dbm: int
    payload_hex: str


@dataclass
class BLEFrame:
    number: int
    timestamp_s: float
    relative_s: float
    mac: str
    payload_length: int
    pdu_type: str
    rssi_raw_dbm: Optional[int]
    rssi_calibrated_dbm: Optional[float]
    channel: Optional[int]
    duration_us: float
    interval_ms: Optional[float]
    company_id_text: str
    raw_data: str
    decoded: DecodedIBeacon


@dataclass
class AdvertisingEvent:
    event_number: int
    start_s: float
    end_s: float
    relative_s: float
    packet_count: int
    channels: str
    rssi_raw_mean: Optional[float]
    rssi_calibrated_mean: Optional[float]
    event_duration_ms: float
    interval_ms: Optional[float]
    uuid: str
    major: int
    minor: int


# ===========================================================================
# Décodage iBeacon
# ===========================================================================

def decode_ibeacon(company_id_text: str, raw_data: str) -> Optional[DecodedIBeacon]:
    """
    Décode le bloc iBeacon dans les champs Manufacturer Specific Data.

    tshark peut retourner le Company ID séparément ou inclure certains octets
    dans le champ data. On cherche donc le motif 02 15 dans la donnée nettoyée.
    """
    raw = normalize_hex(raw_data)
    if not raw:
        return None

    index = raw.find(IBEACON_PREFIX)
    if index < 0:
        return None

    payload = raw[index:]
    # 02 15 + UUID(32) + Major(4) + Minor(4) + Tx(2) = 46 caractères
    if len(payload) < 46:
        return None

    try:
        uuid_hex = payload[4:36]
        major = int(payload[36:40], 16)
        minor = int(payload[40:44], 16)
        tx_power = decode_signed_byte(payload[44:46])
        company = safe_int(company_id_text)
        if company is None:
            company = APPLE_COMPANY_ID
        uuid = normalize_uuid(uuid_hex)
    except (ValueError, IndexError):
        return None

    return DecodedIBeacon(
        company_id=company,
        uuid=uuid,
        major=major,
        minor=minor,
        tx_power_dbm=tx_power,
        payload_hex=payload,
    )


# ===========================================================================
# Émetteur iBeacon BlueZ / bluetoothctl
# ===========================================================================

class IBeaconEmitter:
    def __init__(self, log_callback) -> None:
        self.process: Optional[subprocess.Popen[str]] = None
        self.running = False
        self.config: Optional[IBeaconConfig] = None
        self.log_callback = log_callback

    def _log(self, text: str) -> None:
        self.log_callback(text)

    @staticmethod
    def _check_environment() -> None:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "bluetooth"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("systemctl est introuvable.") from exc

        if result.stdout.strip() != "active":
            raise RuntimeError(
                "Le service Bluetooth n'est pas actif.\n"
                "Commande : sudo systemctl enable --now bluetooth"
            )

        try:
            subprocess.run(
                ["bluetoothctl", "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("bluetoothctl est introuvable.") from exc

    def _reader(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            self._log(line.rstrip())

    def _send(self, command: str, pause: float = 0.35) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Le processus bluetoothctl n'est pas disponible.")
        self._log(f"> {command}")
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        time.sleep(pause)

    def start(self, config: IBeaconConfig) -> None:
        if self.running:
            raise RuntimeError("Une émission iBeacon est déjà active.")

        self._check_environment()

        self.process = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        threading.Thread(target=self._reader, daemon=True).start()

        # Le Company ID Apple est fourni séparément. La donnée commence par 02 15.
        payload = " ".join(f"{byte:02x}" for byte in config.payload_bytes)

        self._send("power on")
        self._send("menu advertise")
        self._send("clear")
        self._send("discoverable off")
        self._send("pairable off")
        self._send(f"interval {config.interval_ms}")

        if config.local_name.strip():
            self._send(f"name {config.local_name.strip()}")
        else:
            self._send("name off")

        # Syntaxe BlueZ récente :
        # manufacturer <company-id> <octet1> <octet2> ...
        self._send(f"manufacturer 0x{APPLE_COMPANY_ID:04x} {payload}", pause=0.8)
        self._send("back")
        self._send("advertise on", pause=1.2)

        if self.process.poll() is not None:
            raise RuntimeError(
                "bluetoothctl s'est arrêté pendant la configuration. "
                "Consulte le journal d'émission."
            )

        self.running = True
        self.config = config
        self._log("Émission iBeacon demandée à BlueZ.")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self._send("advertise off")
                self._send("menu advertise")
                self._send("clear")
                self._send("back")
                self._send("quit")
                self.process.wait(timeout=4)
            except Exception:
                self.process.terminate()

        self.process = None
        self.running = False
        self.config = None
        self._log("Émission arrêtée.")


# ===========================================================================
# Capture nRF Sniffer
# ===========================================================================

class BLECapture:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.queue: queue.Queue[BLEFrame] = queue.Queue()
        self.process: Optional[subprocess.Popen[str]] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.interface_name: Optional[str] = None

        self.frame_count = 0
        self.first_timestamp: Optional[float] = None
        self.last_timestamp: Optional[float] = None

        self.filter_mode = FILTER_UUID_MAJOR_MINOR
        self.target_uuid = DEFAULT_UUID
        self.target_major = DEFAULT_MAJOR
        self.target_minor = DEFAULT_MINOR

        self.rssi_offset_db = 0.0
        self.load_calibration()

    def load_calibration(self) -> None:
        if not CALIBRATION_FILE.exists():
            return
        try:
            data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
            self.rssi_offset_db = float(data.get("offset_db", 0.0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.rssi_offset_db = 0.0

    def save_calibration(
        self,
        reference_rssi_dbm: float,
        measured_rssi_dbm: float,
        samples: int,
    ) -> None:
        data = {
            "reference_rssi_dbm": reference_rssi_dbm,
            "measured_rssi_dbm": measured_rssi_dbm,
            "offset_db": self.rssi_offset_db,
            "samples": samples,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        CALIBRATION_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def calibrate(self, raw_rssi: Optional[int]) -> Optional[float]:
        if raw_rssi is None:
            return None
        return float(raw_rssi) + self.rssi_offset_db

    @staticmethod
    def find_sniffer_interface() -> Optional[str]:
        try:
            result = subprocess.run(
                ["tshark", "-D"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

        preferred: list[str] = []
        fallback: list[str] = []

        for line in result.stdout.splitlines():
            match = re.match(r"^\d+\.\s+(.+?)(?:\s+\(|$)", line.strip())
            if not match:
                continue
            interface = match.group(1).strip()
            lower = line.lower()
            if "nrf sniffer" in lower or "nrf_sniffer" in lower:
                preferred.append(interface)
            elif "/dev/ttyusb" in lower or "/dev/ttyacm" in lower:
                fallback.append(interface)

        return preferred[0] if preferred else (fallback[0] if fallback else None)

    def _matches_filter(self, decoded: DecodedIBeacon) -> bool:
        if self.filter_mode == FILTER_ALL_IBEACONS:
            return True
        if decoded.uuid.lower() != self.target_uuid.lower():
            return False
        if self.filter_mode == FILTER_UUID:
            return True
        return (
            decoded.major == self.target_major
            and decoded.minor == self.target_minor
        )

    def start(self) -> None:
        if self.running:
            return

        self.interface_name = self.find_sniffer_interface()
        if not self.interface_name:
            raise RuntimeError(
                "Aucune interface nRF Sniffer détectée dans 'tshark -D'."
            )

        command = [
            "tshark",
            "-l",
            "-n",
            "-i",
            self.interface_name,
            "-Y",
            "btle",
            "-T",
            "fields",
            "-E",
            "separator=;",
            "-E",
            "occurrence=f",
            "-E",
            "quote=n",
        ]
        for field in TSHARK_FIELDS:
            command.extend(["-e", field])

        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self.running = True
        self.frame_count = 0
        self.first_timestamp = None
        self.last_timestamp = None
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return

        for line in process.stdout:
            if not self.running:
                break

            fields = line.rstrip("\n").split(";")
            fields += [""] * (len(TSHARK_FIELDS) - len(fields))
            (
                timestamp_text,
                mac,
                length_text,
                pdu_type,
                rssi_text,
                channel_text,
                company_id_text,
                raw_data,
            ) = fields[: len(TSHARK_FIELDS)]

            try:
                timestamp = float(timestamp_text)
            except ValueError:
                continue

            decoded = decode_ibeacon(company_id_text, raw_data)
            if decoded is None or not self._matches_filter(decoded):
                continue

            payload_length = safe_int(length_text) or 0
            raw_rssi = safe_int(rssi_text)
            channel = safe_int(channel_text)

            if self.first_timestamp is None:
                self.first_timestamp = timestamp

            interval_ms = None
            if self.last_timestamp is not None:
                interval_ms = (timestamp - self.last_timestamp) * 1000.0
            self.last_timestamp = timestamp

            self.frame_count += 1
            frame = BLEFrame(
                number=self.frame_count,
                timestamp_s=timestamp,
                relative_s=timestamp - self.first_timestamp,
                mac=mac,
                payload_length=payload_length,
                pdu_type=pdu_type,
                rssi_raw_dbm=raw_rssi,
                rssi_calibrated_dbm=self.calibrate(raw_rssi),
                channel=channel,
                duration_us=duration_le1m_us(payload_length),
                interval_ms=interval_ms,
                company_id_text=company_id_text,
                raw_data=normalize_hex(raw_data),
                decoded=decoded,
            )
            self.queue.put(frame)

    def stop(self) -> None:
        self.running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=4)
            except Exception:
                self.process.terminate()
        self.process = None


# ===========================================================================
# Regroupement des canaux 37 / 38 / 39 en événements advertising
# ===========================================================================

def group_advertising_events(
    frames: list[BLEFrame],
    maximum_gap_ms: float = 12.0,
) -> list[AdvertisingEvent]:
    """
    Regroupe des paquets proches ayant le même UUID/Major/Minor.

    Un événement advertising BLE peut contenir un paquet sur chacun des canaux
    37, 38 et 39. La fenêtre de 12 ms reste paramétrable et doit être validée
    expérimentalement selon la configuration du beacon.
    """
    if not frames:
        return []

    ordered = sorted(frames, key=lambda frame: frame.timestamp_s)
    groups: list[list[BLEFrame]] = []
    current: list[BLEFrame] = [ordered[0]]

    for frame in ordered[1:]:
        previous = current[-1]
        same_beacon = (
            frame.decoded.uuid == previous.decoded.uuid
            and frame.decoded.major == previous.decoded.major
            and frame.decoded.minor == previous.decoded.minor
        )
        gap_ms = (frame.timestamp_s - previous.timestamp_s) * 1000.0
        duplicate_channel = frame.channel in {
            packet.channel for packet in current if packet.channel is not None
        }

        if same_beacon and gap_ms <= maximum_gap_ms and not duplicate_channel:
            current.append(frame)
        else:
            groups.append(current)
            current = [frame]
    groups.append(current)

    events: list[AdvertisingEvent] = []
    last_start: Optional[float] = None

    for number, group in enumerate(groups, start=1):
        start = group[0].timestamp_s
        end = group[-1].timestamp_s
        raw_values = [
            float(frame.rssi_raw_dbm)
            for frame in group
            if frame.rssi_raw_dbm is not None
        ]
        calibrated_values = [
            float(frame.rssi_calibrated_dbm)
            for frame in group
            if frame.rssi_calibrated_dbm is not None
        ]
        interval = None if last_start is None else (start - last_start) * 1000.0
        last_start = start

        events.append(
            AdvertisingEvent(
                event_number=number,
                start_s=start,
                end_s=end,
                relative_s=start - ordered[0].timestamp_s,
                packet_count=len(group),
                channels=",".join(
                    str(channel)
                    for channel in sorted(
                        {
                            frame.channel
                            for frame in group
                            if frame.channel is not None
                        }
                    )
                ),
                rssi_raw_mean=mean(raw_values),
                rssi_calibrated_mean=mean(calibrated_values),
                event_duration_ms=(end - start) * 1000.0,
                interval_ms=interval,
                uuid=group[0].decoded.uuid,
                major=group[0].decoded.major,
                minor=group[0].decoded.minor,
            )
        )

    return events


# ===========================================================================
# Interface graphique
# ===========================================================================

class BeaconStudioApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1500x900")
        self.root.minsize(1200, 760)

        acquisition_name = datetime.now().strftime("acquisition_%Y%m%d_%H%M%S")
        self.output_dir = OUTPUT_ROOT / acquisition_name
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.frames: list[BLEFrame] = []
        self.events: list[AdvertisingEvent] = []
        self.capture_running = False
        self.results_saved = False

        self.capture = BLECapture(self.output_dir)
        self.emitter = IBeaconEmitter(self._append_emitter_log)

        self.calibrating = False
        self.calibration_samples: list[float] = []
        self.calibration_target = DEFAULT_CALIBRATION_SAMPLES

        self._create_variables()
        self._build_ui()
        self._schedule_update()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # Variables Tkinter
    # ------------------------------------------------------------------

    def _create_variables(self) -> None:
        self.uuid_var = tk.StringVar(value=DEFAULT_UUID)
        self.major_var = tk.StringVar(value=str(DEFAULT_MAJOR))
        self.minor_var = tk.StringVar(value=str(DEFAULT_MINOR))
        self.tx_power_var = tk.StringVar(value=str(DEFAULT_TX_POWER))
        self.interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_MS))
        self.local_name_var = tk.StringVar(value="Raspberry-iBeacon")
        self.extra_data_var = tk.StringVar(value="")

        self.filter_mode_var = tk.StringVar(value=FILTER_UUID_MAJOR_MINOR)
        self.reference_rssi_var = tk.StringVar(value=str(DEFAULT_REFERENCE_RSSI))
        self.calibration_count_var = tk.StringVar(
            value=str(DEFAULT_CALIBRATION_SAMPLES)
        )
        self.group_gap_var = tk.StringVar(value="12")

        self.emitter_status_var = tk.StringVar(value="Émission arrêtée")
        self.capture_status_var = tk.StringVar(value="Capture arrêtée")
        self.calibration_status_var = tk.StringVar(
            value=f"Offset chargé : {self.capture.rssi_offset_db:+.2f} dB"
        )

        self.stat_frames_var = tk.StringVar(value="Trames : 0")
        self.stat_events_var = tk.StringVar(value="Événements : 0")
        self.stat_rssi_var = tk.StringVar(value="RSSI : --")
        self.stat_interval_var = tk.StringVar(value="Intervalle événement : --")
        self.stat_channels_var = tk.StringVar(value="Canaux 37/38/39 : 0 / 0 / 0")
        self.stat_conformity_var = tk.StringVar(value="Conformité : --")

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.emission_tab = ttk.Frame(self.notebook)
        self.capture_tab = ttk.Frame(self.notebook)
        self.decode_tab = ttk.Frame(self.notebook)
        self.graph_tab = ttk.Frame(self.notebook)
        self.calibration_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.emission_tab, text="1. Émission iBeacon")
        self.notebook.add(self.capture_tab, text="2. Capture")
        self.notebook.add(self.decode_tab, text="3. Décodage / conformité")
        self.notebook.add(self.graph_tab, text="4. Graphiques")
        self.notebook.add(self.calibration_tab, text="5. Calibration / export")

        self._build_emission_tab()
        self._build_capture_tab()
        self._build_decode_tab()
        self._build_graph_tab()
        self._build_calibration_tab()

    def _build_emission_tab(self) -> None:
        panel = ttk.LabelFrame(
            self.emission_tab,
            text="Paramètres du paquet iBeacon",
            padding=14,
        )
        panel.pack(fill="x", padx=12, pady=12)

        fields = [
            ("UUID", self.uuid_var, 42),
            ("Major (0–65535)", self.major_var, 12),
            ("Minor (0–65535)", self.minor_var, 12),
            ("Tx Power à 1 m (dBm)", self.tx_power_var, 12),
            ("Intervalle advertising (ms)", self.interval_var, 12),
            ("Nom local facultatif", self.local_name_var, 28),
            ("Données fabricant supplémentaires (hex)", self.extra_data_var, 42),
        ]

        for row, (label, variable, width) in enumerate(fields):
            ttk.Label(panel, text=label).grid(
                row=row, column=0, sticky="w", padx=6, pady=6
            )
            ttk.Entry(panel, textvariable=variable, width=width).grid(
                row=row, column=1, sticky="w", padx=6, pady=6
            )

        button_frame = ttk.Frame(panel)
        button_frame.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=12)

        self.start_emitter_button = ttk.Button(
            button_frame,
            text="Démarrer l'émission",
            command=self.start_emission,
        )
        self.start_emitter_button.pack(side="left", padx=5)

        self.stop_emitter_button = ttk.Button(
            button_frame,
            text="Arrêter l'émission",
            command=self.stop_emission,
            state="disabled",
        )
        self.stop_emitter_button.pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Afficher le payload",
            command=self.show_payload,
        ).pack(side="left", padx=5)

        ttk.Label(
            panel,
            textvariable=self.emitter_status_var,
        ).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", padx=6)

        payload_frame = ttk.LabelFrame(
            self.emission_tab,
            text="Journal BlueZ / bluetoothctl",
            padding=8,
        )
        payload_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.emitter_log = tk.Text(payload_frame, height=20, wrap="word")
        self.emitter_log.pack(fill="both", expand=True)

    def _build_capture_tab(self) -> None:
        controls = ttk.Frame(self.capture_tab, padding=10)
        controls.pack(fill="x")

        ttk.Label(controls, text="Filtre :").pack(side="left", padx=5)
        self.filter_combo = ttk.Combobox(
            controls,
            textvariable=self.filter_mode_var,
            values=[FILTER_UUID_MAJOR_MINOR, FILTER_UUID, FILTER_ALL_IBEACONS],
            state="readonly",
            width=23,
        )
        self.filter_combo.pack(side="left", padx=5)

        self.start_capture_button = ttk.Button(
            controls,
            text="Démarrer la capture",
            command=self.start_capture,
        )
        self.start_capture_button.pack(side="left", padx=8)

        self.stop_capture_button = ttk.Button(
            controls,
            text="Arrêter et sauvegarder",
            command=self.stop_capture,
            state="disabled",
        )
        self.stop_capture_button.pack(side="left", padx=8)

        ttk.Label(
            controls,
            textvariable=self.capture_status_var,
        ).pack(side="left", padx=15)

        stats = ttk.LabelFrame(self.capture_tab, text="Statistiques temps réel", padding=8)
        stats.pack(fill="x", padx=10, pady=(0, 8))

        for variable in [
            self.stat_frames_var,
            self.stat_events_var,
            self.stat_rssi_var,
            self.stat_interval_var,
            self.stat_channels_var,
            self.stat_conformity_var,
        ]:
            ttk.Label(stats, textvariable=variable).pack(side="left", padx=12)

        columns = (
            "numero",
            "temps",
            "mac",
            "rssi_brut",
            "rssi_calibre",
            "canal",
            "duree",
            "intervalle",
            "uuid",
            "major",
            "minor",
            "tx",
        )
        self.table = ttk.Treeview(
            self.capture_tab,
            columns=columns,
            show="headings",
            height=25,
        )

        headings = {
            "numero": "N°",
            "temps": "Temps (s)",
            "mac": "Adresse",
            "rssi_brut": "RSSI brut",
            "rssi_calibre": "RSSI calibré",
            "canal": "Canal",
            "duree": "Durée (µs)",
            "intervalle": "Δ paquet (ms)",
            "uuid": "UUID",
            "major": "Major",
            "minor": "Minor",
            "tx": "Tx Power",
        }
        widths = {
            "numero": 55,
            "temps": 90,
            "mac": 145,
            "rssi_brut": 85,
            "rssi_calibre": 100,
            "canal": 65,
            "duree": 90,
            "intervalle": 95,
            "uuid": 280,
            "major": 70,
            "minor": 70,
            "tx": 75,
        }

        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], anchor="center")

        scrollbar = ttk.Scrollbar(
            self.capture_tab,
            orient="vertical",
            command=self.table.yview,
        )
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=8)

        self.table.bind("<<TreeviewSelect>>", self.on_frame_selected)

    def _build_decode_tab(self) -> None:
        comparison = ttk.LabelFrame(
            self.decode_tab,
            text="Comparaison données demandées / données reçues",
            padding=10,
        )
        comparison.pack(fill="x", padx=12, pady=12)

        self.comparison_table = ttk.Treeview(
            comparison,
            columns=("champ", "envoye", "recu", "etat"),
            show="headings",
            height=6,
        )
        for column, title, width in [
            ("champ", "Champ", 160),
            ("envoye", "Valeur demandée", 390),
            ("recu", "Valeur reçue", 390),
            ("etat", "Conformité", 120),
        ]:
            self.comparison_table.heading(column, text=title)
            self.comparison_table.column(column, width=width, anchor="center")
        self.comparison_table.pack(fill="x", expand=True)

        details = ttk.LabelFrame(
            self.decode_tab,
            text="Décodage de la trame sélectionnée",
            padding=10,
        )
        details.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.decode_text = tk.Text(details, wrap="word")
        self.decode_text.pack(fill="both", expand=True)

    def _build_graph_tab(self) -> None:
        self.graph_notebook = ttk.Notebook(self.graph_tab)
        self.graph_notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.fig_rssi = Figure(figsize=(10, 6), dpi=100)
        self.ax_rssi = self.fig_rssi.add_subplot(111)
        self.canvas_rssi = self._add_figure_tab(
            self.fig_rssi, "RSSI brut / calibré"
        )

        self.fig_duration = Figure(figsize=(10, 6), dpi=100)
        self.ax_duration = self.fig_duration.add_subplot(111)
        self.canvas_duration = self._add_figure_tab(
            self.fig_duration, "Durée / intervalles"
        )

        self.fig_channels = Figure(figsize=(10, 6), dpi=100)
        self.ax_channels = self.fig_channels.add_subplot(111)
        self.canvas_channels = self._add_figure_tab(
            self.fig_channels, "Canaux 37 / 38 / 39"
        )

        self.fig_hist = Figure(figsize=(10, 6), dpi=100)
        self.ax_hist = self.fig_hist.add_subplot(111)
        self.canvas_hist = self._add_figure_tab(
            self.fig_hist, "Histogramme RSSI"
        )

    def _add_figure_tab(self, figure: Figure, title: str) -> FigureCanvasTkAgg:
        frame = ttk.Frame(self.graph_notebook)
        self.graph_notebook.add(frame, text=title)
        canvas = FigureCanvasTkAgg(figure, master=frame)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return canvas

    def _build_calibration_tab(self) -> None:
        calibration = ttk.LabelFrame(
            self.calibration_tab,
            text="Calibration expérimentale du RSSI à 1 mètre",
            padding=12,
        )
        calibration.pack(fill="x", padx=12, pady=12)

        ttk.Label(calibration, text="RSSI de référence (dBm)").grid(
            row=0, column=0, padx=6, pady=6, sticky="w"
        )
        ttk.Entry(
            calibration,
            textvariable=self.reference_rssi_var,
            width=12,
        ).grid(row=0, column=1, padx=6, pady=6)

        ttk.Label(calibration, text="Nombre d'échantillons").grid(
            row=1, column=0, padx=6, pady=6, sticky="w"
        )
        ttk.Entry(
            calibration,
            textvariable=self.calibration_count_var,
            width=12,
        ).grid(row=1, column=1, padx=6, pady=6)

        self.start_calibration_button = ttk.Button(
            calibration,
            text="Démarrer la calibration",
            command=self.start_calibration,
        )
        self.start_calibration_button.grid(row=2, column=0, padx=6, pady=10)

        ttk.Button(
            calibration,
            text="Réinitialiser l'offset",
            command=self.reset_calibration,
        ).grid(row=2, column=1, padx=6, pady=10)

        ttk.Label(
            calibration,
            textvariable=self.calibration_status_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=6)

        export = ttk.LabelFrame(
            self.calibration_tab,
            text="Regroupement et export",
            padding=12,
        )
        export.pack(fill="x", padx=12, pady=(0, 12))

        ttk.Label(export, text="Fenêtre de regroupement (ms)").pack(
            side="left", padx=6
        )
        ttk.Entry(export, textvariable=self.group_gap_var, width=10).pack(
            side="left", padx=6
        )
        ttk.Button(
            export,
            text="Sauvegarder maintenant",
            command=self.save_all_results_manual,
        ).pack(side="left", padx=10)

        self.export_text = tk.Text(self.calibration_tab, height=20, wrap="word")
        self.export_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.export_text.insert(
            "end",
            "Les fichiers seront enregistrés automatiquement à l'arrêt de la capture.\n"
            f"Dossier courant : {self.output_dir}\n",
        )

    # ------------------------------------------------------------------
    # Émission
    # ------------------------------------------------------------------

    def _read_config(self) -> IBeaconConfig:
        return IBeaconConfig(
            uuid=normalize_uuid(self.uuid_var.get()),
            major=parse_int(self.major_var.get(), "Major", 0, 65535),
            minor=parse_int(self.minor_var.get(), "Minor", 0, 65535),
            tx_power_dbm=parse_int(
                self.tx_power_var.get(), "Tx Power", -128, 127
            ),
            interval_ms=parse_int(
                self.interval_var.get(), "Intervalle", 20, 10240
            ),
            local_name=self.local_name_var.get().strip(),
            extra_manufacturer_hex=normalize_hex(self.extra_data_var.get()),
        )

    def _append_emitter_log(self, text: str) -> None:
        self.root.after(0, self._append_emitter_log_ui, text)

    def _append_emitter_log_ui(self, text: str) -> None:
        self.emitter_log.insert("end", text + "\n")
        self.emitter_log.see("end")

    def show_payload(self) -> None:
        try:
            config = self._read_config()
        except ValueError as error:
            messagebox.showerror("Paramètres invalides", str(error))
            return

        formatted = " ".join(
            f"{byte:02X}" for byte in config.payload_bytes
        )
        messagebox.showinfo(
            "Payload iBeacon",
            (
                f"Company ID : 0x{APPLE_COMPANY_ID:04X}\n"
                f"Manufacturer Data : {formatted}\n\n"
                f"UUID : {config.uuid}\n"
                f"Major : {config.major}\n"
                f"Minor : {config.minor}\n"
                f"Tx Power : {config.tx_power_dbm} dBm"
            ),
        )

    def start_emission(self) -> None:
        try:
            config = self._read_config()
            self.emitter.start(config)
        except Exception as error:
            messagebox.showerror("Émission iBeacon", str(error))
            return

        self.emitter_status_var.set(
            f"Émission active — UUID {config.uuid}, "
            f"Major {config.major}, Minor {config.minor}"
        )
        self.start_emitter_button.configure(state="disabled")
        self.stop_emitter_button.configure(state="normal")
        self._refresh_comparison()

    def stop_emission(self) -> None:
        self.emitter.stop()
        self.emitter_status_var.set("Émission arrêtée")
        self.start_emitter_button.configure(state="normal")
        self.stop_emitter_button.configure(state="disabled")

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def start_capture(self) -> None:
        if self.capture_running:
            return

        try:
            config = self._read_config()
            self.capture.filter_mode = self.filter_mode_var.get()
            self.capture.target_uuid = config.uuid
            self.capture.target_major = config.major
            self.capture.target_minor = config.minor
            self.capture.start()
        except Exception as error:
            messagebox.showerror("Capture BLE", str(error))
            return

        self.capture_running = True
        self.results_saved = False
        self.start_capture_button.configure(state="disabled")
        self.stop_capture_button.configure(state="normal")
        self.filter_combo.configure(state="disabled")
        self.capture_status_var.set(
            f"Capture active — interface {self.capture.interface_name}"
        )

    def stop_capture(self) -> None:
        if not self.capture_running:
            return
        self.capture.stop()
        self.capture_running = False
        self.start_capture_button.configure(state="normal")
        self.stop_capture_button.configure(state="disabled")
        self.filter_combo.configure(state="readonly")

        try:
            self.save_all_results()
            self.capture_status_var.set(
                f"Capture arrêtée — résultats : {self.output_dir}"
            )
            messagebox.showinfo(
                "Acquisition terminée",
                (
                    "Tous les CSV, paramètres et graphes ont été enregistrés.\n\n"
                    f"Dossier : {self.output_dir}"
                ),
            )
        except Exception as error:
            messagebox.showerror("Sauvegarde", str(error))

    def _schedule_update(self) -> None:
        self._drain_queue()
        self.root.after(REFRESH_MS, self._schedule_update)

    def _drain_queue(self) -> None:
        updated = False
        while True:
            try:
                frame = self.capture.queue.get_nowait()
            except queue.Empty:
                break

            self.frames.append(frame)
            self._insert_frame(frame)
            updated = True

            if self.calibrating and frame.rssi_raw_dbm is not None:
                self.calibration_samples.append(float(frame.rssi_raw_dbm))
                self._update_calibration_progress()

        if updated:
            self._recompute_events()
            self._update_statistics()
            self._update_graphs()
            self._refresh_comparison()

    def _insert_frame(self, frame: BLEFrame) -> None:
        self.table.insert(
            "",
            "end",
            values=(
                frame.number,
                f"{frame.relative_s:.3f}",
                frame.mac,
                frame.rssi_raw_dbm if frame.rssi_raw_dbm is not None else "--",
                format_optional(frame.rssi_calibrated_dbm),
                frame.channel if frame.channel is not None else "--",
                f"{frame.duration_us:.1f}",
                format_optional(frame.interval_ms),
                frame.decoded.uuid,
                frame.decoded.major,
                frame.decoded.minor,
                frame.decoded.tx_power_dbm,
            ),
        )
        children = self.table.get_children()
        if len(children) > 1000:
            self.table.delete(children[0])
        if children:
            self.table.see(children[-1])

    def _recompute_events(self) -> None:
        try:
            gap = float(self.group_gap_var.get().replace(",", "."))
        except ValueError:
            gap = 12.0
        self.events = group_advertising_events(self.frames, maximum_gap_ms=gap)

    # ------------------------------------------------------------------
    # Statistiques et conformité
    # ------------------------------------------------------------------

    def _update_statistics(self) -> None:
        raw = [
            float(frame.rssi_raw_dbm)
            for frame in self.frames
            if frame.rssi_raw_dbm is not None
        ]
        calibrated = [
            float(frame.rssi_calibrated_dbm)
            for frame in self.frames
            if frame.rssi_calibrated_dbm is not None
        ]
        event_intervals = [
            event.interval_ms
            for event in self.events
            if event.interval_ms is not None
        ]

        counts = {
            channel: sum(
                1 for frame in self.frames if frame.channel == channel
            )
            for channel in (37, 38, 39)
        }

        self.stat_frames_var.set(f"Trames : {len(self.frames)}")
        self.stat_events_var.set(f"Événements : {len(self.events)}")

        if calibrated:
            self.stat_rssi_var.set(
                f"RSSI calibré : {calibrated[-1]:.1f} dBm "
                f"(moy. {mean(calibrated):.1f})"
            )
        elif raw:
            self.stat_rssi_var.set(f"RSSI brut : {raw[-1]:.1f} dBm")
        else:
            self.stat_rssi_var.set("RSSI : --")

        if event_intervals:
            self.stat_interval_var.set(
                f"Intervalle événement : {mean(event_intervals):.1f} ms"
            )
        else:
            self.stat_interval_var.set("Intervalle événement : --")

        self.stat_channels_var.set(
            f"Canaux 37/38/39 : {counts[37]} / {counts[38]} / {counts[39]}"
        )

        if self.frames:
            config = self._read_config()
            last = self.frames[-1].decoded
            conform = (
                last.uuid.lower() == config.uuid.lower()
                and last.major == config.major
                and last.minor == config.minor
                and last.tx_power_dbm == config.tx_power_dbm
                and last.company_id == APPLE_COMPANY_ID
            )
            self.stat_conformity_var.set(
                "Conformité : conforme" if conform else "Conformité : différence"
            )

    def _refresh_comparison(self) -> None:
        for item in self.comparison_table.get_children():
            self.comparison_table.delete(item)

        try:
            config = self._read_config()
        except ValueError:
            return

        received = self.frames[-1].decoded if self.frames else None
        rows = [
            ("Company ID", f"0x{APPLE_COMPANY_ID:04X}",
             f"0x{received.company_id:04X}" if received else "--",
             received is not None and received.company_id == APPLE_COMPANY_ID),
            ("UUID", config.uuid, received.uuid if received else "--",
             received is not None and received.uuid.lower() == config.uuid.lower()),
            ("Major", str(config.major), str(received.major) if received else "--",
             received is not None and received.major == config.major),
            ("Minor", str(config.minor), str(received.minor) if received else "--",
             received is not None and received.minor == config.minor),
            ("Tx Power", f"{config.tx_power_dbm} dBm",
             f"{received.tx_power_dbm} dBm" if received else "--",
             received is not None and received.tx_power_dbm == config.tx_power_dbm),
        ]

        for field, expected, actual, conforms in rows:
            state = "✓ Conforme" if conforms else ("✗ Différent" if received else "--")
            self.comparison_table.insert(
                "", "end", values=(field, expected, actual, state)
            )

    def on_frame_selected(self, _event=None) -> None:
        selection = self.table.selection()
        if not selection:
            return
        values = self.table.item(selection[0], "values")
        try:
            number = int(values[0])
        except (ValueError, IndexError):
            return

        frame = next(
            (candidate for candidate in self.frames if candidate.number == number),
            None,
        )
        if frame is None:
            return

        decoded = frame.decoded
        text = (
            f"Trame n° {frame.number}\n"
            f"Timestamp : {frame.timestamp_s:.9f} s\n"
            f"Temps relatif : {frame.relative_s:.6f} s\n"
            f"Adresse advertising : {frame.mac}\n"
            f"Type PDU : {frame.pdu_type}\n"
            f"Canal : {frame.channel}\n"
            f"Longueur payload : {frame.payload_length} octets\n"
            f"Durée estimée LE 1M : {frame.duration_us:.3f} µs\n"
            f"Intervalle paquet : {format_optional(frame.interval_ms)} ms\n\n"
            f"RSSI brut : {frame.rssi_raw_dbm} dBm\n"
            f"Offset appliqué : {self.capture.rssi_offset_db:+.3f} dB\n"
            f"RSSI calibré : {format_optional(frame.rssi_calibrated_dbm, 3)} dBm\n\n"
            f"Company ID : 0x{decoded.company_id:04X}\n"
            f"Type beacon : iBeacon\n"
            f"UUID : {decoded.uuid}\n"
            f"Major : {decoded.major}\n"
            f"Minor : {decoded.minor}\n"
            f"Tx Power à 1 m : {decoded.tx_power_dbm} dBm\n\n"
            f"Payload décodé :\n"
            f"02 15 | {uuid_to_hex(decoded.uuid)} | "
            f"{decoded.major:04X} | {decoded.minor:04X} | "
            f"{signed_byte(decoded.tx_power_dbm):02X}\n\n"
            f"Données brutes tshark :\n{frame.raw_data}\n"
        )

        self.decode_text.delete("1.0", "end")
        self.decode_text.insert("end", text)
        self.notebook.select(self.decode_tab)

    # ------------------------------------------------------------------
    # Graphiques temps réel
    # ------------------------------------------------------------------

    def _update_graphs(self) -> None:
        recent = self.frames[-MAX_POINTS:]
        if not recent:
            return

        times = [frame.relative_s for frame in recent]
        raw = [
            float(frame.rssi_raw_dbm)
            if frame.rssi_raw_dbm is not None
            else math.nan
            for frame in recent
        ]
        calibrated = [
            float(frame.rssi_calibrated_dbm)
            if frame.rssi_calibrated_dbm is not None
            else math.nan
            for frame in recent
        ]

        self.ax_rssi.clear()
        self.ax_rssi.plot(times, raw, label="RSSI brut", linewidth=1)
        self.ax_rssi.plot(
            times, calibrated, label="RSSI calibré", linewidth=1
        )
        self.ax_rssi.set_title("RSSI brut et calibré")
        self.ax_rssi.set_xlabel("Temps relatif (s)")
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)
        self.ax_rssi.legend()
        self.fig_rssi.tight_layout()
        self.canvas_rssi.draw_idle()

        self.ax_duration.clear()
        self.ax_duration.plot(
            times,
            [frame.duration_us for frame in recent],
            label="Durée trame (µs)",
            linewidth=1,
        )
        event_x = [
            event.relative_s
            for event in self.events[-MAX_POINTS:]
            if event.interval_ms is not None
        ]
        event_y = [
            event.interval_ms
            for event in self.events[-MAX_POINTS:]
            if event.interval_ms is not None
        ]
        if event_y:
            self.ax_duration.plot(
                event_x, event_y, label="Intervalle événement (ms)", linewidth=1
            )
        self.ax_duration.set_title("Durée des trames et intervalle advertising")
        self.ax_duration.set_xlabel("Temps relatif (s)")
        self.ax_duration.grid(True)
        self.ax_duration.legend()
        self.fig_duration.tight_layout()
        self.canvas_duration.draw_idle()

        self.ax_channels.clear()
        counts = [
            sum(1 for frame in self.frames if frame.channel == channel)
            for channel in (37, 38, 39)
        ]
        self.ax_channels.bar([37, 38, 39], counts)
        self.ax_channels.set_title("Répartition des paquets sur les canaux")
        self.ax_channels.set_xlabel("Canal BLE")
        self.ax_channels.set_ylabel("Nombre de paquets")
        self.ax_channels.set_xticks([37, 38, 39])
        self.fig_channels.tight_layout()
        self.canvas_channels.draw_idle()

        self.ax_hist.clear()
        valid_raw = [value for value in raw if not math.isnan(value)]
        valid_cal = [value for value in calibrated if not math.isnan(value)]
        if valid_raw:
            self.ax_hist.hist(valid_raw, bins=20, alpha=0.55, label="Brut")
        if valid_cal:
            self.ax_hist.hist(valid_cal, bins=20, alpha=0.55, label="Calibré")
        self.ax_hist.set_title("Distribution du RSSI")
        self.ax_hist.set_xlabel("RSSI (dBm)")
        self.ax_hist.set_ylabel("Nombre de paquets")
        self.ax_hist.legend()
        self.fig_hist.tight_layout()
        self.canvas_hist.draw_idle()

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def start_calibration(self) -> None:
        if not self.capture_running:
            messagebox.showwarning(
                "Calibration",
                "Démarre d'abord la capture BLE et place le beacon à 1 mètre.",
            )
            return

        try:
            float(self.reference_rssi_var.get().replace(",", "."))
            count = parse_int(
                self.calibration_count_var.get(),
                "Nombre d'échantillons",
                20,
                10000,
            )
        except ValueError as error:
            messagebox.showerror("Calibration", str(error))
            return

        self.calibration_target = count
        self.calibration_samples.clear()
        self.calibrating = True
        self.start_calibration_button.configure(state="disabled")
        self.calibration_status_var.set(f"Calibration : 0/{count}")

    def _update_calibration_progress(self) -> None:
        count = len(self.calibration_samples)
        self.calibration_status_var.set(
            f"Calibration : {count}/{self.calibration_target}"
        )
        if count >= self.calibration_target:
            self.finish_calibration()

    def finish_calibration(self) -> None:
        self.calibrating = False
        self.start_calibration_button.configure(state="normal")

        reference = float(self.reference_rssi_var.get().replace(",", "."))
        measured = robust_mean(self.calibration_samples)
        self.capture.rssi_offset_db = reference - measured
        self.capture.save_calibration(
            reference_rssi_dbm=reference,
            measured_rssi_dbm=measured,
            samples=len(self.calibration_samples),
        )

        for frame in self.frames:
            frame.rssi_calibrated_dbm = self.capture.calibrate(
                frame.rssi_raw_dbm
            )

        self._recompute_events()
        self._update_statistics()
        self._update_graphs()

        self.calibration_status_var.set(
            f"Calibration terminée — brut {measured:.2f} dBm — "
            f"offset {self.capture.rssi_offset_db:+.2f} dB"
        )
        messagebox.showinfo(
            "Calibration terminée",
            (
                f"Référence : {reference:.2f} dBm\n"
                f"Moyenne brute robuste : {measured:.2f} dBm\n"
                f"Offset : {self.capture.rssi_offset_db:+.2f} dB"
            ),
        )

    def reset_calibration(self) -> None:
        self.calibrating = False
        self.calibration_samples.clear()
        self.capture.rssi_offset_db = 0.0

        for frame in self.frames:
            frame.rssi_calibrated_dbm = (
                float(frame.rssi_raw_dbm)
                if frame.rssi_raw_dbm is not None
                else None
            )

        try:
            CALIBRATION_FILE.unlink(missing_ok=True)
        except OSError:
            pass

        self.start_calibration_button.configure(state="normal")
        self.calibration_status_var.set("Offset réinitialisé : 0.00 dB")
        self._recompute_events()
        self._update_statistics()
        self._update_graphs()

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------

    def save_all_results_manual(self) -> None:
        try:
            self.save_all_results(force=True)
        except Exception as error:
            messagebox.showerror("Export", str(error))
            return
        messagebox.showinfo(
            "Export",
            f"Résultats enregistrés dans :\n{self.output_dir}",
        )

    def save_all_results(self, force: bool = False) -> None:
        if self.results_saved and not force:
            return
        if not self.frames:
            raise RuntimeError("Aucune trame n'a été capturée.")

        self._recompute_events()
        self._write_complete_csv()
        self._write_raw_csv()
        self._write_calibrated_csv()
        self._write_events_csv()
        self._write_statistics_csv()
        self._write_parameters_json()
        self._write_calibration_json()
        self._save_graphs()
        self.results_saved = True

        self.export_text.insert(
            "end",
            f"\nExport terminé : {datetime.now().isoformat(timespec='seconds')}\n"
            f"Dossier : {self.output_dir}\n"
            "- trames_ibeacon_complet.csv\n"
            "- trames_ibeacon_brutes.csv\n"
            "- trames_ibeacon_calibrees.csv\n"
            "- evenements_advertising.csv\n"
            "- statistiques_acquisition.csv\n"
            "- parametres_emission_capture.json\n"
            "- calibration_utilisee.json\n"
            "- graphes/*.png\n",
        )
        self.export_text.see("end")

    def _frame_common_values(self, frame: BLEFrame) -> list:
        return [
            frame.number,
            f"{frame.timestamp_s:.9f}",
            f"{frame.relative_s:.6f}",
            frame.mac,
            frame.payload_length,
            frame.pdu_type,
            "" if frame.channel is None else frame.channel,
            f"{frame.duration_us:.3f}",
            format_optional(frame.interval_ms, 3),
            f"0x{frame.decoded.company_id:04X}",
            frame.decoded.uuid,
            frame.decoded.major,
            frame.decoded.minor,
            frame.decoded.tx_power_dbm,
            frame.raw_data,
        ]

    def _write_complete_csv(self) -> None:
        path = self.output_dir / "trames_ibeacon_complet.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow([
                "numero", "timestamp_s", "temps_relatif_s", "mac",
                "longueur_payload_octets", "type_pdu", "canal",
                "duree_estimee_us", "intervalle_paquet_ms",
                "company_id", "uuid", "major", "minor", "tx_power_dbm",
                "rssi_brut_dbm", "offset_db", "rssi_calibre_dbm",
                "donnees_brutes",
            ])
            for frame in self.frames:
                common = self._frame_common_values(frame)
                raw_data = common.pop()
                writer.writerow(
                    common
                    + [
                        "" if frame.rssi_raw_dbm is None else frame.rssi_raw_dbm,
                        f"{self.capture.rssi_offset_db:.3f}",
                        format_optional(frame.rssi_calibrated_dbm, 3),
                        raw_data,
                    ]
                )

    def _write_raw_csv(self) -> None:
        path = self.output_dir / "trames_ibeacon_brutes.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow([
                "numero", "timestamp_s", "temps_relatif_s", "mac",
                "rssi_brut_dbm", "canal", "longueur_payload_octets",
                "type_pdu", "duree_estimee_us", "intervalle_paquet_ms",
                "uuid", "major", "minor", "tx_power_dbm", "donnees_brutes",
            ])
            for frame in self.frames:
                writer.writerow([
                    frame.number,
                    f"{frame.timestamp_s:.9f}",
                    f"{frame.relative_s:.6f}",
                    frame.mac,
                    "" if frame.rssi_raw_dbm is None else frame.rssi_raw_dbm,
                    "" if frame.channel is None else frame.channel,
                    frame.payload_length,
                    frame.pdu_type,
                    f"{frame.duration_us:.3f}",
                    format_optional(frame.interval_ms, 3),
                    frame.decoded.uuid,
                    frame.decoded.major,
                    frame.decoded.minor,
                    frame.decoded.tx_power_dbm,
                    frame.raw_data,
                ])

    def _write_calibrated_csv(self) -> None:
        path = self.output_dir / "trames_ibeacon_calibrees.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow([
                "numero", "timestamp_s", "temps_relatif_s", "mac",
                "rssi_brut_dbm", "offset_db", "rssi_calibre_dbm",
                "canal", "duree_estimee_us", "intervalle_paquet_ms",
                "uuid", "major", "minor", "tx_power_dbm",
            ])
            for frame in self.frames:
                writer.writerow([
                    frame.number,
                    f"{frame.timestamp_s:.9f}",
                    f"{frame.relative_s:.6f}",
                    frame.mac,
                    "" if frame.rssi_raw_dbm is None else frame.rssi_raw_dbm,
                    f"{self.capture.rssi_offset_db:.3f}",
                    format_optional(frame.rssi_calibrated_dbm, 3),
                    "" if frame.channel is None else frame.channel,
                    f"{frame.duration_us:.3f}",
                    format_optional(frame.interval_ms, 3),
                    frame.decoded.uuid,
                    frame.decoded.major,
                    frame.decoded.minor,
                    frame.decoded.tx_power_dbm,
                ])

    def _write_events_csv(self) -> None:
        path = self.output_dir / "evenements_advertising.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow([
                "numero_evenement", "timestamp_debut_s", "timestamp_fin_s",
                "temps_relatif_s", "nombre_paquets", "canaux",
                "rssi_brut_moyen_dbm", "rssi_calibre_moyen_dbm",
                "duree_evenement_ms", "intervalle_advertising_ms",
                "uuid", "major", "minor",
            ])
            for event in self.events:
                writer.writerow([
                    event.event_number,
                    f"{event.start_s:.9f}",
                    f"{event.end_s:.9f}",
                    f"{event.relative_s:.6f}",
                    event.packet_count,
                    event.channels,
                    format_optional(event.rssi_raw_mean, 3),
                    format_optional(event.rssi_calibrated_mean, 3),
                    f"{event.event_duration_ms:.3f}",
                    format_optional(event.interval_ms, 3),
                    event.uuid,
                    event.major,
                    event.minor,
                ])

    def _write_statistics_csv(self) -> None:
        raw = [
            float(frame.rssi_raw_dbm)
            for frame in self.frames
            if frame.rssi_raw_dbm is not None
        ]
        calibrated = [
            float(frame.rssi_calibrated_dbm)
            for frame in self.frames
            if frame.rssi_calibrated_dbm is not None
        ]
        packet_intervals = [
            frame.interval_ms
            for frame in self.frames
            if frame.interval_ms is not None
        ]
        event_intervals = [
            event.interval_ms
            for event in self.events
            if event.interval_ms is not None
        ]
        durations = [frame.duration_us for frame in self.frames]

        counts = {
            channel: sum(
                1 for frame in self.frames if frame.channel == channel
            )
            for channel in (37, 38, 39)
        }

        rows = [
            ("nombre_trames", len(self.frames), ""),
            ("nombre_evenements", len(self.events), ""),
            ("offset_calibration", self.capture.rssi_offset_db, "dB"),
            ("rssi_brut_moyen", mean(raw), "dBm"),
            ("rssi_brut_min", min(raw) if raw else None, "dBm"),
            ("rssi_brut_max", max(raw) if raw else None, "dBm"),
            ("rssi_brut_ecart_type", std(raw), "dB"),
            ("rssi_calibre_moyen", mean(calibrated), "dBm"),
            ("rssi_calibre_min", min(calibrated) if calibrated else None, "dBm"),
            ("rssi_calibre_max", max(calibrated) if calibrated else None, "dBm"),
            ("rssi_calibre_ecart_type", std(calibrated), "dB"),
            ("duree_trame_moyenne", mean(durations), "us"),
            ("intervalle_paquet_moyen", mean(packet_intervals), "ms"),
            ("intervalle_advertising_moyen", mean(event_intervals), "ms"),
            ("canal_37", counts[37], "paquets"),
            ("canal_38", counts[38], "paquets"),
            ("canal_39", counts[39], "paquets"),
        ]

        path = self.output_dir / "statistiques_acquisition.csv"
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["indicateur", "valeur", "unite"])
            for name, value, unit in rows:
                writer.writerow([
                    name,
                    "" if value is None else (
                        f"{value:.6f}" if isinstance(value, float) else value
                    ),
                    unit,
                ])

    def _write_parameters_json(self) -> None:
        config = self._read_config()
        last = self.frames[-1].decoded if self.frames else None
        data = {
            "date_export": datetime.now().isoformat(timespec="seconds"),
            "dossier": str(self.output_dir),
            "interface_sniffer": self.capture.interface_name,
            "filtre_capture": self.capture.filter_mode,
            "emission": {
                "uuid": config.uuid,
                "major": config.major,
                "minor": config.minor,
                "tx_power_dbm": config.tx_power_dbm,
                "interval_ms": config.interval_ms,
                "local_name": config.local_name,
                "company_id": f"0x{APPLE_COMPANY_ID:04X}",
                "manufacturer_payload_hex": config.payload_hex,
            },
            "derniere_trame_recue": None if last is None else {
                "uuid": last.uuid,
                "major": last.major,
                "minor": last.minor,
                "tx_power_dbm": last.tx_power_dbm,
                "company_id": f"0x{last.company_id:04X}",
            },
            "regroupement_evenements": {
                "fenetre_maximale_ms": float(
                    self.group_gap_var.get().replace(",", ".")
                )
            },
        }
        path = self.output_dir / "parametres_emission_capture.json"
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_calibration_json(self) -> None:
        data = {
            "offset_db": self.capture.rssi_offset_db,
            "fichier_source": str(CALIBRATION_FILE),
            "reference_affichee_dbm": float(
                self.reference_rssi_var.get().replace(",", ".")
            ),
            "date_export": datetime.now().isoformat(timespec="seconds"),
        }
        path = self.output_dir / "calibration_utilisee.json"
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_graphs(self) -> None:
        graph_dir = self.output_dir / "graphes"
        graph_dir.mkdir(parents=True, exist_ok=True)

        times = [frame.relative_s for frame in self.frames]
        raw = [
            float(frame.rssi_raw_dbm)
            if frame.rssi_raw_dbm is not None
            else math.nan
            for frame in self.frames
        ]
        calibrated = [
            float(frame.rssi_calibrated_dbm)
            if frame.rssi_calibrated_dbm is not None
            else math.nan
            for frame in self.frames
        ]
        durations = [frame.duration_us for frame in self.frames]

        def save_figure(filename: str, draw) -> None:
            figure = Figure(figsize=(12, 6), dpi=150)
            axis = figure.add_subplot(111)
            draw(axis)
            figure.tight_layout()
            figure.savefig(graph_dir / filename)

        save_figure(
            "01_rssi_brut_et_calibre.png",
            lambda ax: (
                ax.plot(times, raw, label="RSSI brut", linewidth=1),
                ax.plot(times, calibrated, label="RSSI calibré", linewidth=1),
                ax.set_title("RSSI brut et calibré"),
                ax.set_xlabel("Temps relatif (s)"),
                ax.set_ylabel("RSSI (dBm)"),
                ax.grid(True),
                ax.legend(),
            ),
        )

        save_figure(
            "02_duree_trames.png",
            lambda ax: (
                ax.plot(times, durations, linewidth=1),
                ax.set_title("Durée estimée des trames BLE LE 1M"),
                ax.set_xlabel("Temps relatif (s)"),
                ax.set_ylabel("Durée (µs)"),
                ax.grid(True),
            ),
        )

        valid_raw = [value for value in raw if not math.isnan(value)]
        valid_cal = [value for value in calibrated if not math.isnan(value)]

        def draw_hist(ax) -> None:
            if valid_raw:
                ax.hist(valid_raw, bins=20, alpha=0.55, label="Brut")
            if valid_cal:
                ax.hist(valid_cal, bins=20, alpha=0.55, label="Calibré")
            ax.set_title("Histogramme du RSSI")
            ax.set_xlabel("RSSI (dBm)")
            ax.set_ylabel("Nombre de paquets")
            ax.legend()

        save_figure("03_histogramme_rssi.png", draw_hist)

        save_figure(
            "04_histogramme_durees.png",
            lambda ax: (
                ax.hist(durations, bins=min(20, max(5, len(set(durations))))),
                ax.set_title("Histogramme des durées"),
                ax.set_xlabel("Durée (µs)"),
                ax.set_ylabel("Nombre de paquets"),
            ),
        )

        channel_counts = [
            sum(1 for frame in self.frames if frame.channel == channel)
            for channel in (37, 38, 39)
        ]
        save_figure(
            "05_repartition_canaux.png",
            lambda ax: (
                ax.bar([37, 38, 39], channel_counts),
                ax.set_title("Répartition des canaux advertising"),
                ax.set_xlabel("Canal"),
                ax.set_ylabel("Nombre de paquets"),
                ax.set_xticks([37, 38, 39]),
            ),
        )

        event_x = [
            event.relative_s
            for event in self.events
            if event.interval_ms is not None
        ]
        event_y = [
            event.interval_ms
            for event in self.events
            if event.interval_ms is not None
        ]
        save_figure(
            "06_intervalles_advertising.png",
            lambda ax: (
                ax.plot(event_x, event_y, marker="o", markersize=2, linewidth=1),
                ax.set_title("Intervalle entre événements advertising"),
                ax.set_xlabel("Temps relatif (s)"),
                ax.set_ylabel("Intervalle (ms)"),
                ax.grid(True),
            ),
        )

        save_figure(
            "07_chronologie_trames.png",
            lambda ax: (
                ax.scatter(times, [frame.number for frame in self.frames], s=8),
                ax.set_title("Chronologie des trames reçues"),
                ax.set_xlabel("Temps relatif (s)"),
                ax.set_ylabel("Numéro de trame"),
                ax.grid(True),
            ),
        )

        def draw_channel_rssi(ax) -> None:
            for channel in (37, 38, 39):
                x = [
                    frame.relative_s
                    for frame in self.frames
                    if frame.channel == channel
                    and frame.rssi_calibrated_dbm is not None
                ]
                y = [
                    frame.rssi_calibrated_dbm
                    for frame in self.frames
                    if frame.channel == channel
                    and frame.rssi_calibrated_dbm is not None
                ]
                if x:
                    ax.scatter(x, y, s=10, label=f"Canal {channel}")
            ax.set_title("RSSI calibré selon le canal")
            ax.set_xlabel("Temps relatif (s)")
            ax.set_ylabel("RSSI calibré (dBm)")
            ax.grid(True)
            ax.legend()

        save_figure("08_rssi_par_canal.png", draw_channel_rssi)

    # ------------------------------------------------------------------
    # Fermeture
    # ------------------------------------------------------------------

    def on_close(self) -> None:
        if self.capture_running:
            self.capture.stop()
            self.capture_running = False

        if self.emitter.running:
            self.emitter.stop()

        if self.frames and not self.results_saved:
            try:
                self.save_all_results()
            except Exception:
                pass

        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = BeaconStudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

