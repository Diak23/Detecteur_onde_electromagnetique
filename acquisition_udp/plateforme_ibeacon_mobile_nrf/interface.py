from __future__ import annotations

import queue
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from analysis_ble import (
    calculate_statistics,
    group_ibeacon_events,
)
from capture_nrf import NRFIBeaconCapture
from config import (
    APP_TITLE,
    DEFAULT_GROUP_WINDOW_MS,
    DEFAULT_INTERFACE,
    DEFAULT_TARGET_MAJOR,
    DEFAULT_TARGET_MINOR,
    DEFAULT_TARGET_UUID,
    OUTPUT_DIR,
)
from exports import (
    create_acquisition_folder,
    write_dict_rows,
    write_statistics,
)
from graphs import save_graphs

class IBeaconReceiverApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1350x860")
        self.minsize(1100, 700)

        self.frames = []
        self.events = []
        self.capture = None
        self.queue = queue.Queue()
        self.last_export_folder = None

        self.interface_var = tk.StringVar(
            value=DEFAULT_INTERFACE
        )
        self.window_var = tk.StringVar(
            value=str(DEFAULT_GROUP_WINDOW_MS)
        )
        self.uuid_filter_var = tk.StringVar(
            value=DEFAULT_TARGET_UUID
        )
        self.major_filter_var = tk.StringVar(
            value=DEFAULT_TARGET_MAJOR
        )
        self.minor_filter_var = tk.StringVar(
            value=DEFAULT_TARGET_MINOR
        )
        self.status_var = tk.StringVar(
            value="Capture arrêtée"
        )
        self.frame_count_var = tk.StringVar(
            value="Trames iBeacon : 0"
        )
        self.event_count_var = tk.StringVar(
            value="Événements : 0"
        )
        self.current_beacon_var = tk.StringVar(
            value="Dernier iBeacon : --"
        )
        self.graph_var = tk.StringVar(
            value="RSSI"
        )

        self._build_interface()
        self.after(100, self._process_queue)
        self.protocol(
            "WM_DELETE_WINDOW",
            self.close_application,
        )

    def _build_interface(self):
        header = ttk.Frame(self)
        header.pack(
            fill="x",
            padx=12,
            pady=(12, 4),
        )

        ttk.Label(
            header,
            text="Détecteur iBeacon depuis nRF Connect Mobile",
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
        self.graph_tab = ttk.Frame(notebook)
        self.log_tab = ttk.Frame(notebook)

        notebook.add(
            self.capture_tab,
            text="Détection iBeacon",
        )
        notebook.add(
            self.analysis_tab,
            text="Analyse",
        )
        notebook.add(
            self.graph_tab,
            text="Graphiques",
        )
        notebook.add(
            self.log_tab,
            text="Journal",
        )

        self._build_capture_tab()
        self._build_analysis_tab()
        self._build_graph_tab()
        self._build_log_tab()

    def _add_entry(
        self,
        parent,
        row,
        label,
        variable,
    ):
        ttk.Label(
            parent,
            text=label,
        ).grid(
            row=row,
            column=0,
            sticky="w",
            padx=8,
            pady=5,
        )

        ttk.Entry(
            parent,
            textvariable=variable,
        ).grid(
            row=row,
            column=1,
            sticky="ew",
            padx=8,
            pady=5,
        )

    def _build_capture_tab(self):
        settings = ttk.LabelFrame(
            self.capture_tab,
            text="Configuration de réception",
        )
        settings.pack(
            fill="x",
            padx=12,
            pady=12,
        )
        settings.columnconfigure(1, weight=1)

        self._add_entry(
            settings,
            0,
            "Interface nRF Sniffer",
            self.interface_var,
        )
        self._add_entry(
            settings,
            1,
            "Fenêtre de regroupement (ms)",
            self.window_var,
        )
        self._add_entry(
            settings,
            2,
            "Filtre UUID facultatif",
            self.uuid_filter_var,
        )
        self._add_entry(
            settings,
            3,
            "Filtre Major facultatif",
            self.major_filter_var,
        )
        self._add_entry(
            settings,
            4,
            "Filtre Minor facultatif",
            self.minor_filter_var,
        )

        buttons = ttk.Frame(settings)
        buttons.grid(
            row=5,
            column=0,
            columnspan=2,
            pady=10,
        )

        ttk.Button(
            buttons,
            text="Démarrer la détection",
            command=self.start_capture,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Arrêter et sauvegarder",
            command=self.stop_capture,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Effacer",
            command=self.clear_data,
        ).pack(side="left", padx=5)

        counters = ttk.Frame(self.capture_tab)
        counters.pack(fill="x", padx=12)

        for variable in (
            self.frame_count_var,
            self.event_count_var,
            self.current_beacon_var,
        ):
            ttk.Label(
                counters,
                textvariable=variable,
                font=("TkDefaultFont", 10, "bold"),
            ).pack(
                side="left",
                padx=15,
                pady=5,
            )

        columns = (
            "time",
            "uuid",
            "major",
            "minor",
            "tx_power",
            "address",
            "rssi",
            "channel",
            "pdu",
            "length",
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
            "tx_power": "Tx Power",
            "address": "Adresse BLE",
            "rssi": "RSSI",
            "channel": "Canal",
            "pdu": "PDU",
            "length": "Longueur",
        }

        widths = {
            "time": 145,
            "uuid": 295,
            "major": 65,
            "minor": 65,
            "tx_power": 75,
            "address": 145,
            "rssi": 70,
            "channel": 65,
            "pdu": 90,
            "length": 70,
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

        scrollbar = ttk.Scrollbar(
            self.capture_tab,
            orient="vertical",
            command=self.frame_table.yview,
        )
        self.frame_table.configure(
            yscrollcommand=scrollbar.set
        )

        self.frame_table.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(12, 0),
            pady=12,
        )
        scrollbar.pack(
            side="right",
            fill="y",
            padx=(0, 12),
            pady=12,
        )

    def _build_analysis_tab(self):
        ttk.Button(
            self.analysis_tab,
            text="Recalculer l'analyse",
            command=self.analyse,
        ).pack(
            anchor="w",
            padx=12,
            pady=10,
        )

        self.statistics_text = tk.Text(
            self.analysis_tab,
            height=14,
            wrap="word",
        )
        self.statistics_text.pack(
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

        widths = {
            "index": 50,
            "uuid": 300,
            "major": 65,
            "minor": 65,
            "duration": 100,
            "interval": 110,
            "packets": 75,
            "channels": 90,
            "rssi": 90,
        }

        for column in columns:
            self.event_table.heading(
                column,
                text=headings[column],
            )
            self.event_table.column(
                column,
                width=widths[column],
                anchor="center",
            )

        self.event_table.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=(0, 12),
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
        ).pack(side="left")

        selector = ttk.Combobox(
            controls,
            textvariable=self.graph_var,
            values=(
                "RSSI",
                "Intervalles",
                "Durées",
                "Canaux",
            ),
            state="readonly",
            width=20,
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
        ).pack(side="left")

        self.figure = Figure(
            figsize=(10, 6),
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

    def _build_log_tab(self):
        self.log_text = tk.Text(
            self.log_tab,
            wrap="word",
        )
        self.log_text.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    def queue_frame(self, frame):
        self.queue.put(("frame", frame))

    def queue_log(self, message):
        self.queue.put(("log", str(message)))

    def _process_queue(self):
        while True:
            try:
                event_type, data = self.queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "frame":
                self._receive_frame(data)
            elif event_type == "log":
                self._write_log(data)

        self.after(100, self._process_queue)

    def _write_log(self, message):
        self.log_text.insert(
            "end",
            message + "\n",
        )
        self.log_text.see("end")

    def _matches_filters(self, frame):
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

    def _receive_frame(self, frame):
        if not self._matches_filters(frame):
            return

        self.frames.append(frame)

        self.frame_count_var.set(
            f"Trames iBeacon : {len(self.frames)}"
        )
        self.current_beacon_var.set(
            f"Dernier iBeacon : Major {frame.major}, Minor {frame.minor}"
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
                frame.pdu_type,
                (
                    ""
                    if frame.length is None
                    else frame.length
                ),
            ),
        )

        if len(self.frames) % 20 == 0:
            self.analyse()

    def start_capture(self):
        if self.capture is not None:
            messagebox.showwarning(
                "Capture",
                "La détection est déjà active.",
            )
            return

        try:
            self.capture = NRFIBeaconCapture(
                interface=self.interface_var.get().strip(),
                on_frame=self.queue_frame,
                on_log=self.queue_log,
            )
            self.capture.start()

            self.status_var.set(
                "Détection iBeacon active"
            )
            self._write_log(
                "Détection démarrée sur "
                + self.interface_var.get().strip()
            )
            self._write_log(
                "Le programme affiche uniquement "
                "les trames iBeacon valides."
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
            try:
                self.capture.stop()
            finally:
                self.capture = None

        self.status_var.set(
            "Capture arrêtée"
        )
        self._write_log(
            "Détection arrêtée."
        )

        self.analyse()

        if self.frames:
            self.export_results(silent=True)

    def clear_data(self):
        if self.capture is not None:
            messagebox.showwarning(
                "Capture",
                "Arrête la capture avant d'effacer.",
            )
            return

        self.frames.clear()
        self.events.clear()

        for item in self.frame_table.get_children():
            self.frame_table.delete(item)

        for item in self.event_table.get_children():
            self.event_table.delete(item)

        self.statistics_text.delete(
            "1.0",
            "end",
        )

        self.axis.clear()
        self.canvas.draw_idle()

        self.frame_count_var.set(
            "Trames iBeacon : 0"
        )
        self.event_count_var.set(
            "Événements : 0"
        )
        self.current_beacon_var.set(
            "Dernier iBeacon : --"
        )

    def _group_window(self):
        try:
            value = float(
                self.window_var
                .get()
                .replace(",", ".")
            )
        except ValueError as exc:
            raise ValueError(
                "La fenêtre de regroupement "
                "doit être numérique."
            ) from exc

        if value <= 0:
            raise ValueError(
                "La fenêtre de regroupement "
                "doit être positive."
            )

        return value

    def analyse(self):
        try:
            window_ms = self._group_window()
        except ValueError as exc:
            messagebox.showerror(
                "Analyse",
                str(exc),
            )
            return

        self.events = group_ibeacon_events(
            self.frames,
            window_ms=window_ms,
        )

        stats = calculate_statistics(
            self.frames,
            self.events,
        )

        self.event_count_var.set(
            f"Événements : {len(self.events)}"
        )

        self.statistics_text.delete(
            "1.0",
            "end",
        )

        for key, value in stats.items():
            if isinstance(value, float):
                display = f"{value:.4f}"
            else:
                display = str(value)

            self.statistics_text.insert(
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

        self.draw_graph()

    def draw_graph(self):
        self.axis.clear()

        graph_name = self.graph_var.get()

        if graph_name == "RSSI":
            valid = [
                frame
                for frame in self.frames
                if frame.rssi is not None
            ]

            if valid:
                t0 = valid[0].timestamp
                self.axis.plot(
                    [
                        frame.timestamp - t0
                        for frame in valid
                    ],
                    [
                        frame.rssi
                        for frame in valid
                    ],
                )
                self.axis.set_xlabel(
                    "Temps (s)"
                )
                self.axis.set_ylabel(
                    "RSSI (dBm)"
                )
                self.axis.set_title(
                    "RSSI des iBeacons reçus"
                )

        elif graph_name == "Intervalles":
            intervals = [
                event.interval_ms
                for event in self.events
                if event.interval_ms is not None
            ]

            if intervals:
                self.axis.plot(
                    range(1, len(intervals) + 1),
                    intervals,
                    marker="o",
                )
                self.axis.set_xlabel(
                    "Événement"
                )
                self.axis.set_ylabel(
                    "Intervalle (ms)"
                )
                self.axis.set_title(
                    "Intervalle entre advertisements"
                )

        elif graph_name == "Durées":
            durations = [
                event.duration_ms
                for event in self.events
            ]

            if durations:
                self.axis.hist(
                    durations,
                    bins=min(
                        20,
                        max(5, len(durations)),
                    ),
                )
                self.axis.set_xlabel(
                    "Durée (ms)"
                )
                self.axis.set_ylabel(
                    "Nombre"
                )
                self.axis.set_title(
                    "Durée des événements iBeacon"
                )

        elif graph_name == "Canaux":
            counts = [
                sum(
                    1
                    for frame in self.frames
                    if frame.channel == channel
                )
                for channel in (37, 38, 39)
            ]

            self.axis.bar(
                ["37", "38", "39"],
                counts,
            )
            self.axis.set_xlabel(
                "Canal BLE"
            )
            self.axis.set_ylabel(
                "Nombre de trames"
            )
            self.axis.set_title(
                "Répartition des canaux iBeacon"
            )

        self.axis.grid(True)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def export_results(self, silent=False):
        if not self.frames:
            if not silent:
                messagebox.showwarning(
                    "Export",
                    "Aucune trame iBeacon détectée.",
                )
            return

        self.analyse()

        folder = create_acquisition_folder(
            OUTPUT_DIR
        )

        stats = calculate_statistics(
            self.frames,
            self.events,
        )

        write_dict_rows(
            folder / "trames_ibeacon.csv",
            [
                frame.as_dict()
                for frame in self.frames
            ],
        )

        write_dict_rows(
            folder / "evenements_ibeacon.csv",
            [
                event.as_dict()
                for event in self.events
            ],
        )

        write_statistics(
            folder / "statistiques_ibeacon.csv",
            stats,
        )

        graph_paths = save_graphs(
            self.frames,
            self.events,
            folder / "graphes",
        )

        self.last_export_folder = folder

        self._write_log(
            "Résultats sauvegardés dans : "
            + str(folder)
        )
        self._write_log(
            f"{len(graph_paths)} graphe(s) sauvegardé(s)."
        )

        if not silent:
            messagebox.showinfo(
                "Export terminé",
                str(folder),
            )

    def close_application(self):
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass

        self.destroy()
