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
matplotlib.use('Agg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

APP_TITLE = 'Plateforme iBeacon V4 — Analyse énergétique'
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / 'acquisitions_ibeacon_v4'
IBEACON_HEX_LEN = 46


def clean_hex(value: str) -> str:
    return re.sub(r'[^0-9a-fA-F]', '', value or '').lower()


def signed8(value: int) -> int:
    return value - 256 if value > 127 else value


def format_uuid(raw: bytes) -> str:
    h = raw.hex()
    return f'{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}'


def decode_ibeacon(value: str):
    h = clean_hex(value)
    positions = []
    for marker in ('4c000215', '004c0215'):
        pos = h.find(marker)
        if pos >= 0:
            positions.append(pos + 4)
    start = 0
    while True:
        pos = h.find('0215', start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 2
    for pos in dict.fromkeys(positions):
        body = h[pos:pos + IBEACON_HEX_LEN]
        if len(body) != IBEACON_HEX_LEN or not body.startswith('0215'):
            continue
        try:
            raw = bytes.fromhex(body)
        except ValueError:
            continue
        if len(raw) != 23:
            continue
        return {
            'uuid': format_uuid(raw[2:18]),
            'major': int.from_bytes(raw[18:20], 'big'),
            'minor': int.from_bytes(raw[20:22], 'big'),
            'tx_power_dbm': signed8(raw[22]),
            'raw_hex': body,
        }
    return None


def parse_float(value):
    try:
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def parse_int(value):
    try:
        return int(str(value), 0)
    except Exception:
        return None


def dbm_to_watts(dbm: float) -> float:
    return 10 ** ((dbm - 30.0) / 10.0)


def estimate_airtime_us(length: Optional[int], phy: str, fallback_us: float) -> float:
    if length is None or length < 0:
        return fallback_us
    if phy == 'LE 1M':
        return (length + 10) * 8.0
    if phy == 'LE 2M':
        return (length + 11) * 4.0
    return fallback_us


@dataclass
class Frame:
    timestamp: float
    address: str
    length: Optional[int]
    rssi_dbm: Optional[float]
    channel: Optional[int]
    uuid: str
    major: int
    minor: int
    tx_power_dbm: int
    raw_hex: str
    calibrated_rssi_dbm: Optional[float] = None
    power_w: Optional[float] = None
    power_nw: Optional[float] = None
    airtime_us: Optional[float] = None
    energy_j: Optional[float] = None
    energy_nj: Optional[float] = None


@dataclass
class Event:
    index: int
    uuid: str
    major: int
    minor: int
    address: str
    start_epoch: float
    end_epoch: float
    span_ms: float
    interval_ms: Optional[float]
    packet_count: int
    channels: str
    rssi_mean_dbm: Optional[float]
    total_airtime_us: float
    energy_j: float
    energy_nj: float
    cumulative_energy_nj: float


class Capture:
    META = [
        'frame.time_epoch',
        'btle.advertising_address',
        'btle.length',
        'nordic_ble.rssi',
        'nordic_ble.channel',
    ]
    RAW = [
        'btcommon.eir_ad.entry.data',
        'btcommon.eir_ad.entry.service_data',
        'btle.advertising_data',
        'btle.data',
        'data.data',
    ]

    def __init__(self, interface, on_frame, on_log, on_raw):
        self.interface = interface
        self.on_frame = on_frame
        self.on_log = on_log
        self.on_raw = on_raw
        self.process = None
        self.stop_event = threading.Event()

    @staticmethod
    def list_interfaces():
        result = subprocess.run(['tshark', '-D'], capture_output=True, text=True, timeout=20)
        return [line.split('. ', 1)[1].strip() for line in result.stdout.splitlines() if '. ' in line]

    @staticmethod
    def available_fields():
        result = subprocess.run(['tshark', '-G', 'fields'], capture_output=True, text=True, timeout=30)
        fields = set()
        for line in result.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 3 and parts[0] == 'F':
                fields.add(parts[2])
        return fields

    def start(self):
        if shutil.which('tshark') is None:
            raise RuntimeError('tshark est introuvable.')
        available = self.available_fields()
        meta = [f for f in self.META if f in available]
        raw = [f for f in self.RAW if f in available]
        if 'frame.time_epoch' not in meta or not raw:
            raise RuntimeError('Champs tshark BLE insuffisants.')
        fields = meta + raw
        cmd = ['tshark', '-l', '-n', '-i', self.interface, '-Y', 'btle', '-T', 'fields',
               '-E', 'separator=;', '-E', 'occurrence=a', '-E', 'aggregator=|', '-E', 'quote=n']
        for field in fields:
            cmd += ['-e', field]
        self.on_log('Commande : ' + ' '.join(cmd))
        self.stop_event.clear()
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._stdout, args=(fields, meta, raw), daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _stdout(self, fields, meta, raw):
        index = {field: i for i, field in enumerate(fields)}
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            if self.stop_event.is_set():
                break
            parts = line.rstrip('\n').split(';')
            parts += [''] * (len(fields) - len(parts))
            timestamp = parse_float(parts[index['frame.time_epoch']])
            if timestamp is None:
                continue
            decoded = None
            for field in raw:
                for candidate in parts[index[field]].split('|'):
                    decoded = decode_ibeacon(candidate)
                    if decoded:
                        break
                if decoded:
                    break
            if not decoded:
                diagnostic = ' || '.join(f'{f}={parts[index[f]]}' for f in raw if parts[index[f]])
                if diagnostic:
                    self.on_raw(diagnostic)
                continue
            self.on_frame(Frame(
                timestamp=timestamp,
                address=parts[index['btle.advertising_address']] if 'btle.advertising_address' in index else '',
                length=parse_int(parts[index['btle.length']]) if 'btle.length' in index else None,
                rssi_dbm=parse_float(parts[index['nordic_ble.rssi']]) if 'nordic_ble.rssi' in index else None,
                channel=parse_int(parts[index['nordic_ble.channel']]) if 'nordic_ble.channel' in index else None,
                uuid=decoded['uuid'], major=decoded['major'], minor=decoded['minor'],
                tx_power_dbm=decoded['tx_power_dbm'], raw_hex=decoded['raw_hex']))

    def _stderr(self):
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            if line.strip():
                self.on_log('tshark : ' + line.strip())

    def stop(self):
        self.stop_event.set()
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


def enrich_frame(frame: Frame, phy: str, fallback_us: float, offset_db: float):
    frame.airtime_us = estimate_airtime_us(frame.length, phy, fallback_us)
    if frame.rssi_dbm is None:
        return
    frame.calibrated_rssi_dbm = frame.rssi_dbm + offset_db
    frame.power_w = dbm_to_watts(frame.calibrated_rssi_dbm)
    frame.power_nw = frame.power_w * 1e9
    frame.energy_j = frame.power_w * frame.airtime_us * 1e-6
    frame.energy_nj = frame.energy_j * 1e9


def group_events(frames, window_ms):
    groups = []
    for frame in sorted(frames, key=lambda f: f.timestamp):
        identity = (frame.uuid, frame.major, frame.minor, frame.address)
        if not groups:
            groups.append([frame])
            continue
        prev = groups[-1][-1]
        prev_identity = (prev.uuid, prev.major, prev.minor, prev.address)
        gap_ms = (frame.timestamp - prev.timestamp) * 1000
        if identity == prev_identity and gap_ms <= window_ms:
            groups[-1].append(frame)
        else:
            groups.append([frame])

    previous_start = {}
    cumulative = defaultdict(float)
    events = []
    for idx, group in enumerate(groups, start=1):
        first, last = group[0], group[-1]
        identity = (first.uuid, first.major, first.minor, first.address)
        interval = None
        if identity in previous_start:
            interval = (first.timestamp - previous_start[identity]) * 1000
        previous_start[identity] = first.timestamp
        energy_j = sum(f.energy_j or 0.0 for f in group)
        cumulative[identity] += energy_j
        rssi = [f.calibrated_rssi_dbm for f in group if f.calibrated_rssi_dbm is not None]
        channels = sorted({f.channel for f in group if f.channel is not None})
        events.append(Event(
            index=idx, uuid=first.uuid, major=first.major, minor=first.minor,
            address=first.address, start_epoch=first.timestamp, end_epoch=last.timestamp,
            span_ms=(last.timestamp - first.timestamp) * 1000,
            interval_ms=interval, packet_count=len(group),
            channels=','.join(map(str, channels)),
            rssi_mean_dbm=mean(rssi) if rssi else None,
            total_airtime_us=sum(f.airtime_us or 0.0 for f in group),
            energy_j=energy_j, energy_nj=energy_j * 1e9,
            cumulative_energy_nj=cumulative[identity] * 1e9))
    return events


def stats_by_uuid(frames, events):
    fb = defaultdict(list)
    eb = defaultdict(list)
    for f in frames:
        fb[f.uuid].append(f)
    for e in events:
        eb[e.uuid].append(e)
    rows = []
    for uuid in sorted(fb):
        fs, es = fb[uuid], eb[uuid]
        rssi = [f.calibrated_rssi_dbm for f in fs if f.calibrated_rssi_dbm is not None]
        powers = [f.power_nw for f in fs if f.power_nw is not None]
        intervals = [e.interval_ms for e in es if e.interval_ms is not None]
        rows.append({
            'uuid': uuid,
            'nombre_trames': len(fs),
            'nombre_evenements': len(es),
            'rssi_moyen_dbm': mean(rssi) if rssi else None,
            'rssi_min_dbm': min(rssi) if rssi else None,
            'rssi_max_dbm': max(rssi) if rssi else None,
            'puissance_moyenne_nw': mean(powers) if powers else None,
            'airtime_total_us': sum(f.airtime_us or 0 for f in fs),
            'energie_totale_j': sum(f.energy_j or 0 for f in fs),
            'energie_totale_nj': sum(f.energy_nj or 0 for f in fs),
            'intervalle_moyen_ms': mean(intervals) if intervals else None,
            'canal_37': sum(1 for f in fs if f.channel == 37),
            'canal_38': sum(1 for f in fs if f.channel == 38),
            'canal_39': sum(1 for f in fs if f.channel == 39),
        })
    return rows


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('1500x930')
        self.frames, self.events = [], []
        self.capture = None
        self.q = queue.Queue()

        self.interface_var = tk.StringVar(value='/dev/ttyUSB0-4.4')
        self.window_var = tk.StringVar(value='20')
        self.phy_var = tk.StringVar(value='LE 1M')
        self.fallback_var = tk.StringVar(value='376')
        self.offset_var = tk.StringVar(value='0')
        self.graph_var = tk.StringVar(value='RSSI par UUID')
        self.status_var = tk.StringVar(value='Capture arrêtée')
        self.summary_var = tk.StringVar(value='Trames: 0 | Événements: 0 | Énergie: 0 nJ')

        self._ui()
        self.refresh_interfaces()
        self.after(100, self.process_queue)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def _ui(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=10, pady=8)
        ttk.Label(top, text=APP_TITLE, font=('TkDefaultFont', 16, 'bold')).pack(side='left')
        ttk.Label(top, textvariable=self.status_var).pack(side='right')

        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True, padx=10, pady=8)
        self.capture_tab, self.events_tab, self.energy_tab, self.graph_tab, self.log_tab = [ttk.Frame(nb) for _ in range(5)]
        for tab, title in zip((self.capture_tab, self.events_tab, self.energy_tab, self.graph_tab, self.log_tab),
                              ('Détection', 'Événements', 'Énergie par UUID', 'Graphiques', 'Journal')):
            nb.add(tab, text=title)

        cfg = ttk.LabelFrame(self.capture_tab, text='Configuration')
        cfg.pack(fill='x', padx=10, pady=10)
        cfg.columnconfigure(1, weight=1)
        rows = [
            ('Interface nRF Sniffer', self.interface_var),
            ('Fenêtre regroupement (ms)', self.window_var),
            ('PHY', self.phy_var),
            ('Durée de repli (µs)', self.fallback_var),
            ('Correction RSSI (dB)', self.offset_var),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(cfg, text=label).grid(row=i, column=0, padx=6, pady=4)
            if label == 'PHY':
                w = ttk.Combobox(cfg, textvariable=var, values=('LE 1M', 'LE 2M', 'LE Coded'), state='readonly')
            elif i == 0:
                self.interface_combo = ttk.Combobox(cfg, textvariable=var)
                w = self.interface_combo
            else:
                w = ttk.Entry(cfg, textvariable=var)
            w.grid(row=i, column=1, sticky='ew', padx=6, pady=4)
        ttk.Button(cfg, text='Actualiser interfaces', command=self.refresh_interfaces).grid(row=0, column=2, padx=6)
        btns = ttk.Frame(cfg)
        btns.grid(row=5, column=0, columnspan=3, pady=8)
        ttk.Button(btns, text='Démarrer', command=self.start_capture).pack(side='left', padx=5)
        ttk.Button(btns, text='Arrêter et sauvegarder', command=self.stop_capture).pack(side='left', padx=5)
        ttk.Button(btns, text='Effacer', command=self.clear).pack(side='left', padx=5)
        ttk.Label(self.capture_tab, textvariable=self.summary_var, font=('TkDefaultFont', 11, 'bold')).pack(anchor='w', padx=12)

        self.frame_table = self.make_table(self.capture_tab,
            ('time','uuid','major','minor','rssi','power','airtime','energy','channel'),
            ('Temps','UUID','Major','Minor','RSSI dBm','Puissance nW','Durée µs','Énergie nJ','Canal'))
        self.event_table = self.make_table(self.events_tab,
            ('idx','uuid','span','interval','packets','airtime','energy','cumulative'),
            ('N°','UUID','Étendue ms','Intervalle ms','Paquets','Durée RF µs','Énergie nJ','Cumul nJ'))
        self.energy_table = self.make_table(self.energy_tab,
            ('uuid','frames','events','rssi','power','airtime','energy_nj','energy_uj'),
            ('UUID','Trames','Événements','RSSI moyen','Puissance nW','Durée RF µs','Énergie nJ','Énergie µJ'))

        controls = ttk.Frame(self.graph_tab)
        controls.pack(fill='x', padx=10, pady=8)
        ttk.Combobox(controls, textvariable=self.graph_var, state='readonly', width=30,
                     values=('RSSI par UUID','Puissance par UUID','Énergie par trame','Énergie cumulée par UUID','Intervalles par UUID')).pack(side='left')
        ttk.Button(controls, text='Tracer', command=self.draw_graph).pack(side='left', padx=8)
        self.figure = Figure(figsize=(11,7), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.graph_tab)
        self.canvas.get_tk_widget().pack(fill='both', expand=True, padx=10, pady=10)

        self.log_text = tk.Text(self.log_tab)
        self.log_text.pack(fill='both', expand=True, padx=10, pady=10)

    def make_table(self, parent, columns, headings):
        table = ttk.Treeview(parent, columns=columns, show='headings')
        for c, h in zip(columns, headings):
            table.heading(c, text=h)
            table.column(c, width=300 if c == 'uuid' else 120, anchor='center')
        table.pack(fill='both', expand=True, padx=10, pady=10)
        return table

    def refresh_interfaces(self):
        try:
            values = Capture.list_interfaces()
            self.interface_combo['values'] = values
            matches = [v for v in values if 'nrf' in v.lower() or 'ttyusb' in v.lower()]
            if matches:
                self.interface_var.set(matches[0].split(' (',1)[0])
        except Exception as exc:
            self.log(str(exc))

    def settings(self):
        return self.phy_var.get(), float(self.fallback_var.get().replace(',','.')), float(self.offset_var.get().replace(',','.'))

    def start_capture(self):
        if self.capture:
            return
        try:
            self.settings()
            self.capture = Capture(self.interface_var.get().strip(),
                                   lambda f: self.q.put(('frame',f)),
                                   lambda m: self.q.put(('log',m)),
                                   lambda m: self.q.put(('raw',m)))
            self.capture.start()
            self.status_var.set('Capture active')
        except Exception as exc:
            self.capture = None
            messagebox.showerror('Erreur', str(exc))

    def stop_capture(self):
        if self.capture:
            self.capture.stop()
            self.capture = None
        self.status_var.set('Capture arrêtée')
        self.analyse()
        if self.frames:
            self.export()

    def process_queue(self):
        while True:
            try:
                kind, value = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == 'frame':
                self.add_frame(value)
            elif kind == 'log':
                self.log(value)
        self.after(100, self.process_queue)

    def add_frame(self, frame):
        phy, fallback, offset = self.settings()
        enrich_frame(frame, phy, fallback, offset)
        self.frames.append(frame)
        self.frame_table.insert('', 'end', values=(
            f'{frame.timestamp:.6f}', frame.uuid, frame.major, frame.minor,
            self.fmt(frame.calibrated_rssi_dbm), self.fmt(frame.power_nw),
            self.fmt(frame.airtime_us), self.fmt(frame.energy_nj), frame.channel or ''))
        if len(self.frames) % 20 == 0:
            self.analyse()
        self.update_summary()

    def analyse(self):
        self.events = group_events(self.frames, float(self.window_var.get().replace(',','.')))
        for table in (self.event_table, self.energy_table):
            for item in table.get_children():
                table.delete(item)
        for e in self.events:
            self.event_table.insert('', 'end', values=(e.index,e.uuid,f'{e.span_ms:.3f}',
                '' if e.interval_ms is None else f'{e.interval_ms:.3f}',e.packet_count,
                f'{e.total_airtime_us:.2f}',f'{e.energy_nj:.8g}',f'{e.cumulative_energy_nj:.8g}'))
        for row in stats_by_uuid(self.frames, self.events):
            self.energy_table.insert('', 'end', values=(row['uuid'],row['nombre_trames'],row['nombre_evenements'],
                self.fmt(row['rssi_moyen_dbm']),self.fmt(row['puissance_moyenne_nw']),
                self.fmt(row['airtime_total_us']),self.fmt(row['energie_totale_nj']),
                self.fmt(row['energie_totale_j']*1e6)))
        self.update_summary()
        self.draw_graph()

    def update_summary(self):
        total = sum(f.energy_nj or 0 for f in self.frames)
        self.summary_var.set(f'Trames: {len(self.frames)} | Événements: {len(self.events)} | UUID: {len({f.uuid for f in self.frames})} | Énergie reçue: {total:.8g} nJ')

    def draw_graph(self):
        self.axis.clear()
        grouped = defaultdict(list)
        title = self.graph_var.get()
        if title == 'RSSI par UUID':
            for f in self.frames:
                if f.calibrated_rssi_dbm is not None: grouped[f.uuid].append(f)
            self.plot_time(grouped, lambda f:f.calibrated_rssi_dbm, 'RSSI (dBm)', title)
        elif title == 'Puissance par UUID':
            for f in self.frames:
                if f.power_nw is not None: grouped[f.uuid].append(f)
            self.plot_time(grouped, lambda f:f.power_nw, 'Puissance (nW)', title)
        elif title == 'Énergie par trame':
            for f in self.frames:
                if f.energy_nj is not None: grouped[f.uuid].append(f)
            for uuid, values in sorted(grouped.items()):
                self.axis.plot(range(1,len(values)+1), [f.energy_nj for f in values], marker='o', markersize=3, label=uuid)
            self.axis.set_xlabel('Numéro de trame'); self.axis.set_ylabel('Énergie (nJ)'); self.axis.set_title(title)
        elif title == 'Énergie cumulée par UUID':
            for e in self.events: grouped[e.uuid].append(e)
            for uuid, values in sorted(grouped.items()):
                self.axis.plot(range(1,len(values)+1), [e.cumulative_energy_nj for e in values], marker='o', markersize=3, label=uuid)
            self.axis.set_xlabel("Numéro d'événement"); self.axis.set_ylabel('Énergie cumulée (nJ)'); self.axis.set_title(title)
        else:
            for e in self.events:
                if e.interval_ms is not None: grouped[e.uuid].append(e)
            for uuid, values in sorted(grouped.items()):
                self.axis.plot(range(1,len(values)+1), [e.interval_ms for e in values], marker='o', markersize=3, label=uuid)
            self.axis.set_xlabel("Numéro d'événement"); self.axis.set_ylabel('Intervalle (ms)'); self.axis.set_title(title)
        if grouped:
            self.axis.legend(fontsize=8)
        self.axis.grid(True)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def plot_time(self, grouped, getter, ylabel, title):
        if not grouped: return
        start = min(f.timestamp for values in grouped.values() for f in values)
        for uuid, values in sorted(grouped.items()):
            values = sorted(values, key=lambda f:f.timestamp)
            self.axis.plot([f.timestamp-start for f in values],[getter(f) for f in values],marker='o',markersize=3,label=uuid)
        self.axis.set_xlabel('Temps (s)'); self.axis.set_ylabel(ylabel); self.axis.set_title(title)

    def export(self):
        folder = OUTPUT_DIR / ('acquisition_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        graphs = folder / 'graphes'
        graphs.mkdir(parents=True, exist_ok=True)
        self.write_rows(folder/'trames_energie.csv',[asdict(f) for f in self.frames])
        self.write_rows(folder/'evenements_energie.csv',[asdict(e) for e in self.events])
        self.write_rows(folder/'energie_par_uuid.csv',stats_by_uuid(self.frames,self.events))
        original = self.graph_var.get()
        for name, filename in [('RSSI par UUID','rssi_par_uuid.png'),('Puissance par UUID','puissance_par_uuid.png'),('Énergie par trame','energie_par_trame.png'),('Énergie cumulée par UUID','energie_cumulee_par_uuid.png'),('Intervalles par UUID','intervalles_par_uuid.png')]:
            self.graph_var.set(name); self.draw_graph(); self.figure.savefig(graphs/filename,dpi=180,bbox_inches='tight')
        self.graph_var.set(original); self.draw_graph()
        messagebox.showinfo('Export terminé', str(folder))

    @staticmethod
    def write_rows(path, rows):
        if not rows:
            path.write_text('',encoding='utf-8'); return
        with path.open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0].keys()),delimiter=';'); writer.writeheader(); writer.writerows(rows)

    @staticmethod
    def fmt(value):
        return '' if value is None else f'{value:.8g}'

    def clear(self):
        if self.capture:
            messagebox.showwarning('Capture','Arrêtez la capture avant de nettoyer.'); return
        self.frames.clear(); self.events.clear()
        for table in (self.frame_table,self.event_table,self.energy_table):
            for item in table.get_children(): table.delete(item)
        self.update_summary(); self.axis.clear(); self.canvas.draw_idle()

    def log(self, message):
        self.log_text.insert('end',str(message)+'\n'); self.log_text.see('end')

    def close(self):
        if self.capture: self.capture.stop()
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
