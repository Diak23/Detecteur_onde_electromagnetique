#!/usr/bin/env python3
"""
Interface d'acquisition BLE pour le beacon emis par le Raspberry Pi.

Fonctions :
- detection automatique de l'interface nRF Sniffer dans tshark ;
- capture des trames BLE advertising ;
- filtrage au choix par MAC, UUID, UUID+Major+Minor ou tous les iBeacons ;
- extraction du RSSI, canal, longueur, type PDU et timestamp ;
- calcul de la duree estimee de la trame en LE 1M ;
- calcul de l'intervalle entre deux trames ;
- affichage temps reel dans une interface Tkinter ;
- sauvegarde automatique CSV ;
- graphes :
    1. RSSI et duree de trame ;
    2. histogramme des durees et repartition des canaux ;
    3. chronologie des trames.

Prerequis :
    sudo apt install -y tshark python3-tk python3-matplotlib
    pip install pandas matplotlib

Lancement :
    python3 interface_ibeacon_nrf51822_v2.py
"""

from __future__ import annotations

import csv
import os
import queue
import re
import signal
import subprocess
import sys
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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_MAC = "DC:A6:32:65:E5:71".lower()
TARGET_UUID = "e20a39f4-73f5-4bc4-a12f-17d1ad07a961"
TARGET_MAJOR = 0
TARGET_MINOR = 0

# Modes de filtrage disponibles dans l'interface
FILTER_MAC = "Adresse MAC"
FILTER_UUID = "UUID"
FILTER_UUID_MAJOR_MINOR = "UUID + Major + Minor"
FILTER_ALL_IBEACONS = "Tous les iBeacons"

OUTPUT_ROOT = Path("acquisitions_ibeacon")
REFRESH_MS = 500
MAX_POINTS = 300

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


@dataclass
class BLEFrame:
    number: int
    timestamp_s: float
    relative_s: float
    mac: str
    payload_length: int
    pdu_type: str
    rssi_dbm: Optional[int]
    channel: Optional[int]
    duration_us: float
    end_timestamp_s: float
    interval_ms: Optional[float]
    company_id: str
    raw_data: str


class BLECapture:
    def __init__(self, target_mac: str, output_dir: Path) -> None:
        self.target_mac = target_mac.lower()
        self.output_dir = output_dir
        self.csv_path = output_dir / "trames_ibeacon.csv"

        self.process: Optional[subprocess.Popen[str]] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.queue: queue.Queue[BLEFrame] = queue.Queue()

        self.frame_count = 0
        self.first_timestamp: Optional[float] = None
        self.last_timestamp: Optional[float] = None
        self.interface_name: Optional[str] = None

        # Le mode UUID + Major + Minor est recommandé pour Android, car
        # nRF Connect utilise généralement une adresse BLE aléatoire.
        self.filter_mode = FILTER_UUID_MAJOR_MINOR

    @staticmethod
    def find_sniffer_interface() -> Optional[str]:
        try:
            result = subprocess.run(
                ["tshark", "-D"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

        preferred = []
        fallback = []

        for line in result.stdout.splitlines():
            match = re.match(r"^\d+\.\s+(.+?)(?:\s+\(|$)", line.strip())
            if not match:
                continue

            iface = match.group(1).strip()
            lower = line.lower()

            if "nrf sniffer" in lower or "nrf_sniffer" in lower:
                preferred.append(iface)
            elif "/dev/ttyusb" in lower or "/dev/ttyacm" in lower:
                fallback.append(iface)

        if preferred:
            return preferred[0]
        if fallback:
            return fallback[0]
        return None

    def _build_command(self) -> list[str]:
        if not self.interface_name:
            raise RuntimeError("Interface nRF Sniffer non definie.")

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

        return command

    def start(self) -> None:
        if self.running:
            return

        self.interface_name = self.find_sniffer_interface()
        if not self.interface_name:
            raise RuntimeError(
                "Aucune interface nRF Sniffer detectee. "
                "Execute d'abord : tshark -D"
            )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_csv()

        command = self._build_command()

        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self.running = True
        self.thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.running = False

        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()

        self.process = None

    def _initialize_csv(self) -> None:
        with self.csv_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(
                [
                    "numero",
                    "timestamp_s",
                    "temps_relatif_s",
                    "mac",
                    "longueur_payload_octets",
                    "type_pdu",
                    "rssi_dbm",
                    "canal",
                    "duree_estimee_us",
                    "timestamp_fin_s",
                    "intervalle_ms",
                    "company_id",
                    "donnees_brutes",
                    "uuid_cible",
                    "major_cible",
                    "minor_cible",
                ]
            )

    @staticmethod
    def _safe_int(value: str) -> Optional[int]:
        if not value:
            return None

        value = value.strip()

        try:
            if value.lower().startswith("0x"):
                return int(value, 16)
            return int(float(value))
        except ValueError:
            return None

    @staticmethod
    def _duration_le1m_us(payload_length: int) -> float:
        # Trame legacy LE 1M :
        # preambule 1 + access address 4 + header 2 + payload L + CRC 3
        return float((10 + payload_length) * 8)

    @staticmethod
    def _normalize_hex(value: str) -> str:
        """Supprime 0x, séparateurs et caractères non hexadécimaux."""
        value = value.replace("0x", "").replace(":", "").replace("-", "")
        return re.sub(r"[^0-9a-fA-F]", "", value).lower()

    def _is_target_ibeacon(
        self,
        mac: str,
        company_id: str,
        raw_data: str,
    ) -> bool:
        """Vérifie si la trame correspond au mode de filtrage choisi."""
        normalized_mac = mac.lower().strip()

        # Signature iBeacon : Apple 0x004C + type 0x02 + longueur 0x15.
        combined = self._normalize_hex(company_id) + self._normalize_hex(raw_data)
        is_ibeacon = "4c000215" in combined or "0215" in combined
        if not is_ibeacon:
            return False

        if self.filter_mode == FILTER_ALL_IBEACONS:
            return True

        if self.filter_mode == FILTER_MAC:
            return normalized_mac == self.target_mac

        target_uuid_hex = TARGET_UUID.replace("-", "").lower()
        if target_uuid_hex not in combined:
            return False

        if self.filter_mode == FILTER_UUID:
            return True

        # Major et Minor sont codés sur 2 octets chacun, juste après l'UUID.
        expected = (
            target_uuid_hex
            + f"{TARGET_MAJOR:04x}"
            + f"{TARGET_MINOR:04x}"
        )
        return expected in combined

    def _read_loop(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        for raw_line in self.process.stdout:
            if not self.running:
                break

            line = raw_line.strip()
            if not line:
                continue

            parts = line.split(";")
            while len(parts) < len(TSHARK_FIELDS):
                parts.append("")

            (
                time_epoch,
                mac,
                length,
                pdu_type,
                rssi,
                channel,
                company_id,
                raw_data,
            ) = parts[: len(TSHARK_FIELDS)]

            if not time_epoch or not mac:
                continue

            normalized_mac = mac.lower().strip()
            if not self._is_target_ibeacon(
                normalized_mac,
                company_id,
                raw_data,
            ):
                continue

            try:
                timestamp_s = float(time_epoch)
            except ValueError:
                continue

            payload_length = self._safe_int(length)
            if payload_length is None:
                payload_length = 0

            rssi_dbm = self._safe_int(rssi)
            channel_value = self._safe_int(channel)

            if self.first_timestamp is None:
                self.first_timestamp = timestamp_s

            interval_ms = None
            if self.last_timestamp is not None:
                interval_ms = (timestamp_s - self.last_timestamp) * 1000.0

            self.last_timestamp = timestamp_s
            self.frame_count += 1

            duration_us = self._duration_le1m_us(payload_length)

            frame = BLEFrame(
                number=self.frame_count,
                timestamp_s=timestamp_s,
                relative_s=timestamp_s - self.first_timestamp,
                mac=mac,
                payload_length=payload_length,
                pdu_type=pdu_type or "Inconnu",
                rssi_dbm=rssi_dbm,
                channel=channel_value,
                duration_us=duration_us,
                end_timestamp_s=timestamp_s + duration_us / 1_000_000.0,
                interval_ms=interval_ms,
                company_id=company_id,
                raw_data=raw_data,
            )

            self._append_csv(frame)
            self.queue.put(frame)

    def _append_csv(self, frame: BLEFrame) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(
                [
                    frame.number,
                    f"{frame.timestamp_s:.9f}",
                    f"{frame.relative_s:.6f}",
                    frame.mac,
                    frame.payload_length,
                    frame.pdu_type,
                    "" if frame.rssi_dbm is None else frame.rssi_dbm,
                    "" if frame.channel is None else frame.channel,
                    f"{frame.duration_us:.3f}",
                    f"{frame.end_timestamp_s:.9f}",
                    "" if frame.interval_ms is None else f"{frame.interval_ms:.3f}",
                    frame.company_id,
                    frame.raw_data,
                    TARGET_UUID,
                    TARGET_MAJOR,
                    TARGET_MINOR,
                ]
            )


class IBeaconApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Analyse iBeacon - nRF51822")
        self.root.geometry("1400x850")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = OUTPUT_ROOT / f"acquisition_{timestamp}"
        self.capture = BLECapture(TARGET_MAC, self.output_dir)

        self.frames: list[BLEFrame] = []
        self.running = False
        self.filter_mode_var = tk.StringVar(value=FILTER_UUID_MAJOR_MINOR)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(REFRESH_MS, self.update_ui)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="iBeacon Raspberry Pi",
            font=("TkDefaultFont", 16, "bold"),
        ).pack(side="left", padx=(0, 20))

        self.start_button = ttk.Button(
            top,
            text="Demarrer l'acquisition",
            command=self.start_capture,
        )
        self.start_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(
            top,
            text="Arreter",
            command=self.stop_capture,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=5)

        self.status_label = ttk.Label(top, text="Etat : arrete")
        self.status_label.pack(side="left", padx=20)

        self.info_label = ttk.Label(
            top,
            text=(
                f"MAC cible : {TARGET_MAC.upper()} | "
                f"UUID : {TARGET_UUID} | Major : {TARGET_MAJOR} | Minor : {TARGET_MINOR}"
            ),
        )
        self.info_label.pack(side="right")

        filter_frame = ttk.LabelFrame(
            self.root,
            text="Filtrage des iBeacons",
            padding=8,
        )
        filter_frame.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(filter_frame, text="Mode :").pack(side="left", padx=5)
        self.filter_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_mode_var,
            values=(
                FILTER_MAC,
                FILTER_UUID,
                FILTER_UUID_MAJOR_MINOR,
                FILTER_ALL_IBEACONS,
            ),
            state="readonly",
            width=24,
        )
        self.filter_combo.pack(side="left", padx=5)

        ttk.Label(
            filter_frame,
            text=(
                "Pour nRF Connect Mobile avec RANDOM ADDRESS, choisis "
                "UUID + Major + Minor ou Tous les iBeacons."
            ),
        ).pack(side="left", padx=15)

        stats = ttk.LabelFrame(self.root, text="Statistiques", padding=8)
        stats.pack(fill="x", padx=8, pady=(0, 8))

        self.stat_vars = {
            "frames": tk.StringVar(value="Trames : 0"),
            "rssi": tk.StringVar(value="RSSI : -- dBm"),
            "duration": tk.StringVar(value="Duree : -- us"),
            "interval": tk.StringVar(value="Intervalle : -- ms"),
            "channel": tk.StringVar(value="Canal : --"),
        }

        for var in self.stat_vars.values():
            ttk.Label(
                stats,
                textvariable=var,
                font=("TkDefaultFont", 11, "bold"),
            ).pack(side="left", padx=20)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_live = ttk.Frame(notebook)
        self.tab_distribution = ttk.Frame(notebook)
        self.tab_timeline = ttk.Frame(notebook)
        self.tab_table = ttk.Frame(notebook)

        notebook.add(self.tab_live, text="RSSI et duree")
        notebook.add(self.tab_distribution, text="Histogramme et canaux")
        notebook.add(self.tab_timeline, text="Chronologie")
        notebook.add(self.tab_table, text="Trames")

        self._build_live_tab()
        self._build_distribution_tab()
        self._build_timeline_tab()
        self._build_table_tab()

    def _build_live_tab(self) -> None:
        self.live_fig = Figure(figsize=(11, 7), dpi=100)

        self.ax_rssi = self.live_fig.add_subplot(211)
        self.ax_duration = self.live_fig.add_subplot(212)

        self.ax_rssi.set_title("RSSI en fonction du temps")
        self.ax_rssi.set_xlabel("Temps relatif (s)")
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)

        self.ax_duration.set_title("Duree estimee des trames")
        self.ax_duration.set_xlabel("Temps relatif (s)")
        self.ax_duration.set_ylabel("Duree (us)")
        self.ax_duration.grid(True)

        self.live_canvas = FigureCanvasTkAgg(
            self.live_fig,
            master=self.tab_live,
        )
        self.live_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_distribution_tab(self) -> None:
        self.dist_fig = Figure(figsize=(11, 7), dpi=100)

        self.ax_hist = self.dist_fig.add_subplot(211)
        self.ax_channels = self.dist_fig.add_subplot(212)

        self.ax_hist.set_title("Histogramme des durees")
        self.ax_hist.set_xlabel("Duree (us)")
        self.ax_hist.set_ylabel("Nombre de trames")

        self.ax_channels.set_title("Repartition des canaux")
        self.ax_channels.set_xlabel("Canal BLE")
        self.ax_channels.set_ylabel("Nombre de trames")

        self.dist_canvas = FigureCanvasTkAgg(
            self.dist_fig,
            master=self.tab_distribution,
        )
        self.dist_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_timeline_tab(self) -> None:
        self.timeline_fig = Figure(figsize=(11, 7), dpi=100)
        self.ax_timeline = self.timeline_fig.add_subplot(111)

        self.ax_timeline.set_title("Chronologie des trames recues")
        self.ax_timeline.set_xlabel("Temps relatif (s)")
        self.ax_timeline.set_ylabel("Numero de trame")
        self.ax_timeline.grid(True)

        self.timeline_canvas = FigureCanvasTkAgg(
            self.timeline_fig,
            master=self.tab_timeline,
        )
        self.timeline_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_table_tab(self) -> None:
        columns = (
            "numero",
            "temps",
            "mac",
            "rssi",
            "canal",
            "longueur",
            "duree",
            "intervalle",
            "pdu",
        )

        self.tree = ttk.Treeview(
            self.tab_table,
            columns=columns,
            show="headings",
        )

        headings = {
            "numero": "No",
            "temps": "Temps (s)",
            "mac": "Adresse MAC",
            "rssi": "RSSI (dBm)",
            "canal": "Canal",
            "longueur": "Longueur",
            "duree": "Duree (us)",
            "intervalle": "Intervalle (ms)",
            "pdu": "Type PDU",
        }

        widths = {
            "numero": 70,
            "temps": 110,
            "mac": 160,
            "rssi": 100,
            "canal": 80,
            "longueur": 90,
            "duree": 100,
            "intervalle": 120,
            "pdu": 160,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor="center",
            )

        scrollbar = ttk.Scrollbar(
            self.tab_table,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def start_capture(self) -> None:
        if self.running:
            return

        try:
            self.capture.filter_mode = self.filter_mode_var.get()
            self.capture.start()
        except Exception as error:
            messagebox.showerror(
                "Erreur de capture",
                str(error),
            )
            return

        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.filter_combo.configure(state="disabled")
        self.status_label.configure(
            text=(
                f"Etat : capture sur {self.capture.interface_name} | "
                f"Filtre : {self.capture.filter_mode}"
            )
        )

    def stop_capture(self) -> None:
        if not self.running:
            return

        self.capture.stop()
        self.running = False

        # Récupère les dernières trames encore présentes dans la file.
        while True:
            try:
                frame = self.capture.queue.get_nowait()
            except queue.Empty:
                break

            self.frames.append(frame)
            self._insert_table_row(frame)

        # Met à jour une dernière fois les statistiques et les graphes,
        # puis les sauvegarde automatiquement.
        if self.frames:
            self._update_statistics()
            self._update_plots()
            self.root.update_idletasks()
            self._save_graphs()

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.filter_combo.configure(state="readonly")
        self.status_label.configure(
            text=(
                f"Etat : arrete | CSV : {self.capture.csv_path} | "
                f"Graphes : {self.output_dir}"
            )
        )

        messagebox.showinfo(
            "Acquisition sauvegardee",
            (
                "L'acquisition est terminee.\n\n"
                f"CSV : {self.capture.csv_path}\n"
                f"Graphes : {self.output_dir}"
            ),
        )

    def update_ui(self) -> None:
        new_frames = []

        while True:
            try:
                frame = self.capture.queue.get_nowait()
            except queue.Empty:
                break

            self.frames.append(frame)
            new_frames.append(frame)
            self._insert_table_row(frame)

        if new_frames:
            self._update_statistics()
            self._update_plots()

        self.root.after(REFRESH_MS, self.update_ui)

    def _insert_table_row(self, frame: BLEFrame) -> None:
        interval = (
            "--"
            if frame.interval_ms is None
            else f"{frame.interval_ms:.2f}"
        )

        self.tree.insert(
            "",
            0,
            values=(
                frame.number,
                f"{frame.relative_s:.3f}",
                frame.mac,
                "--" if frame.rssi_dbm is None else frame.rssi_dbm,
                "--" if frame.channel is None else frame.channel,
                frame.payload_length,
                f"{frame.duration_us:.1f}",
                interval,
                frame.pdu_type,
            ),
        )

        children = self.tree.get_children()
        if len(children) > 500:
            self.tree.delete(children[-1])

    def _update_statistics(self) -> None:
        frame = self.frames[-1]

        rssis = [
            item.rssi_dbm
            for item in self.frames
            if item.rssi_dbm is not None
        ]
        intervals = [
            item.interval_ms
            for item in self.frames
            if item.interval_ms is not None
        ]

        self.stat_vars["frames"].set(
            f"Trames : {len(self.frames)}"
        )
        self.stat_vars["rssi"].set(
            "RSSI : -- dBm"
            if not rssis
            else f"RSSI : {rssis[-1]} dBm | moy. {sum(rssis)/len(rssis):.1f}"
        )
        self.stat_vars["duration"].set(
            f"Duree : {frame.duration_us:.1f} us"
        )
        self.stat_vars["interval"].set(
            "Intervalle : -- ms"
            if not intervals
            else (
                f"Intervalle : {intervals[-1]:.1f} ms | "
                f"moy. {sum(intervals)/len(intervals):.1f}"
            )
        )
        self.stat_vars["channel"].set(
            "Canal : --"
            if frame.channel is None
            else f"Canal : {frame.channel}"
        )

    def _save_graphs(self) -> None:
        """Sauvegarde automatiquement tous les graphes au format PNG."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.live_fig.savefig(
            self.output_dir / "rssi_et_duree.png",
            dpi=200,
            bbox_inches="tight",
        )
        self.dist_fig.savefig(
            self.output_dir / "histogramme_et_canaux.png",
            dpi=200,
            bbox_inches="tight",
        )
        self.timeline_fig.savefig(
            self.output_dir / "chronologie_trames.png",
            dpi=200,
            bbox_inches="tight",
        )

    def _update_plots(self) -> None:
        data = self.frames[-MAX_POINTS:]

        times = [frame.relative_s for frame in data]
        durations = [frame.duration_us for frame in data]

        rssi_times = [
            frame.relative_s
            for frame in data
            if frame.rssi_dbm is not None
        ]
        rssis = [
            frame.rssi_dbm
            for frame in data
            if frame.rssi_dbm is not None
        ]

        self.ax_rssi.clear()
        self.ax_rssi.set_title("RSSI en fonction du temps")
        self.ax_rssi.set_xlabel("Temps relatif (s)")
        self.ax_rssi.set_ylabel("RSSI (dBm)")
        self.ax_rssi.grid(True)
        if rssis:
            self.ax_rssi.plot(rssi_times, rssis, marker="o", markersize=3)

        self.ax_duration.clear()
        self.ax_duration.set_title("Duree estimee des trames")
        self.ax_duration.set_xlabel("Temps relatif (s)")
        self.ax_duration.set_ylabel("Duree (us)")
        self.ax_duration.grid(True)
        if durations:
            self.ax_duration.plot(times, durations, marker="o", markersize=3)

        self.live_fig.tight_layout()
        self.live_canvas.draw_idle()

        self.ax_hist.clear()
        self.ax_hist.set_title("Histogramme des durees")
        self.ax_hist.set_xlabel("Duree (us)")
        self.ax_hist.set_ylabel("Nombre de trames")
        if durations:
            bins = min(20, max(5, len(set(durations))))
            self.ax_hist.hist(durations, bins=bins)

        channel_counts = {37: 0, 38: 0, 39: 0}
        for frame in self.frames:
            if frame.channel in channel_counts:
                channel_counts[frame.channel] += 1

        self.ax_channels.clear()
        self.ax_channels.set_title("Repartition des canaux")
        self.ax_channels.set_xlabel("Canal BLE")
        self.ax_channels.set_ylabel("Nombre de trames")
        self.ax_channels.bar(
            list(channel_counts.keys()),
            list(channel_counts.values()),
        )
        self.ax_channels.set_xticks([37, 38, 39])

        self.dist_fig.tight_layout()
        self.dist_canvas.draw_idle()

        self.ax_timeline.clear()
        self.ax_timeline.set_title("Chronologie des trames recues")
        self.ax_timeline.set_xlabel("Temps relatif (s)")
        self.ax_timeline.set_ylabel("Numero de trame")
        self.ax_timeline.grid(True)

        if data:
            self.ax_timeline.scatter(
                [frame.relative_s for frame in data],
                [frame.number for frame in data],
                s=15,
            )

        self.timeline_fig.tight_layout()
        self.timeline_canvas.draw_idle()

    def on_close(self) -> None:
        if self.running:
            self.stop_capture()

        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = IBeaconApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
