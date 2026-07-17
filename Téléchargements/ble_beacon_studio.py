#!/usr/bin/env python3
from __future__ import annotations

import csv
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

OUTPUT_ROOT = Path('acquisitions_ble_beacons')
REFRESH_MS = 500
MAX_POINTS = 400

TSHARK_FIELDS = [
    'frame.time_epoch',
    'btle.advertising_address',
    'btle.length',
    'btle.advertising_header.pdu_type',
    'nordic_ble.rssi',
    'nordic_ble.channel',
    'btcommon.eir_ad.entry.company_id',
    'btcommon.eir_ad.entry.data',
    'btcommon.eir_ad.entry.service_uuid_16',
    'btcommon.eir_ad.entry.service_data',
]


def normalize_hex(value: str) -> str:
    value = value.replace('0x', '').replace(':', '').replace('-', '').replace(' ', '')
    return re.sub(r'[^0-9a-fA-F]', '', value).lower()


def safe_int(value: str) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value, 16) if value.lower().startswith('0x') else int(float(value))
    except ValueError:
        return None


def signed_byte(value: int) -> int:
    if not -128 <= value <= 127:
        raise ValueError('Tx Power doit être compris entre -128 et 127 dBm.')
    return value & 0xFF


def decode_signed_byte(hex_byte: str) -> int:
    value = int(hex_byte, 16)
    return value - 256 if value >= 128 else value


def duration_le1m_us(payload_length: int) -> float:
    return float((10 + payload_length) * 8)


URL_SCHEMES = {
    'http://www.': 0x00,
    'https://www.': 0x01,
    'http://': 0x02,
    'https://': 0x03,
}

URL_EXPANSIONS = {
    '.com/': 0x00, '.org/': 0x01, '.edu/': 0x02, '.net/': 0x03,
    '.info/': 0x04, '.biz/': 0x05, '.gov/': 0x06,
    '.com': 0x07, '.org': 0x08, '.edu': 0x09, '.net': 0x0A,
    '.info': 0x0B, '.biz': 0x0C, '.gov': 0x0D,
}


def build_eddystone_uid(namespace: str, instance: str, tx_power: int) -> list[int]:
    ns = normalize_hex(namespace)
    ins = normalize_hex(instance)
    if len(ns) != 20:
        raise ValueError('Namespace : exactement 10 octets, soit 20 caractères hexadécimaux.')
    if len(ins) != 12:
        raise ValueError('Instance : exactement 6 octets, soit 12 caractères hexadécimaux.')
    return [0x00, signed_byte(tx_power)] + list(bytes.fromhex(ns)) + list(bytes.fromhex(ins)) + [0x00, 0x00]


def build_eddystone_url(url: str, tx_power: int) -> list[int]:
    url = url.strip()
    scheme = None
    rest = ''
    for prefix, code in URL_SCHEMES.items():
        if url.startswith(prefix):
            scheme = code
            rest = url[len(prefix):]
            break
    if scheme is None:
        raise ValueError('URL invalide : utilise http:// ou https://.')

    result: list[int] = []
    i = 0
    expansions = sorted(URL_EXPANSIONS.items(), key=lambda item: len(item[0]), reverse=True)
    while i < len(rest):
        for text, code in expansions:
            if rest.startswith(text, i):
                result.append(code)
                i += len(text)
                break
        else:
            result.extend(rest[i].encode('utf-8'))
            i += 1

    if len(result) > 17:
        raise ValueError('URL trop longue pour une trame Eddystone-URL legacy.')
    return [0x10, signed_byte(tx_power), scheme] + result


class EddystoneEmitter:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen[str]] = None
        self.running = False
        self.mode = ''

    @staticmethod
    def _check_environment() -> None:
        service = subprocess.run(['systemctl', 'is-active', 'bluetooth'], capture_output=True, text=True)
        if service.stdout.strip() != 'active':
            raise RuntimeError('Bluetooth n’est pas actif. Exécute : sudo systemctl start bluetooth')
        try:
            subprocess.run(['bluetoothctl', '--version'], check=True, capture_output=True, text=True)
        except Exception as exc:
            raise RuntimeError('bluetoothctl est introuvable.') from exc

    def start_uid(self, namespace: str, instance: str, tx_power: int, interval_ms: int) -> None:
        data = build_eddystone_uid(namespace, instance, tx_power)
        self._start('Eddystone-UID', data, interval_ms)

    def start_url(self, url: str, tx_power: int, interval_ms: int) -> None:
        data = build_eddystone_url(url, tx_power)
        self._start('Eddystone-URL', data, interval_ms)

    def _start(self, mode: str, data: list[int], interval_ms: int) -> None:
        if self.running:
            raise RuntimeError('Une émission est déjà active.')
        if not 20 <= interval_ms <= 10240:
            raise ValueError('Intervalle autorisé : 20 à 10240 ms.')

        self._check_environment()
        self.process = subprocess.Popen(
            ['bluetoothctl'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        assert self.process.stdin is not None

        commands = [
            'power on',
            'menu advertise',
            'clear',
            'name off',
            f'interval {interval_ms}',
            'uuids 0xfeaa',
            'service 0xfeaa ' + ' '.join(f'{byte:02x}' for byte in data),
            'back',
            'advertise on',
        ]
        for command in commands:
            self.process.stdin.write(command + '\n')
            self.process.stdin.flush()
            time.sleep(0.3)

        time.sleep(1)
        if self.process.poll() is not None:
            output = self.process.stdout.read() if self.process.stdout else ''
            self.process = None
            raise RuntimeError('Échec de configuration bluetoothctl :\n' + output)

        self.running = True
        self.mode = mode

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                for command in ['advertise off', 'menu advertise', 'clear', 'back', 'quit']:
                    self.process.stdin.write(command + '\n')
                    self.process.stdin.flush()
                    time.sleep(0.15)
                self.process.wait(timeout=4)
            except Exception:
                self.process.terminate()
        self.process = None
        self.running = False
        self.mode = ''


@dataclass
class DecodedEddystone:
    frame_type: str
    identifier: str
    tx_power_dbm: int
    namespace: str = ''
    instance: str = ''
    url: str = ''


@dataclass
class BLEFrame:
    number: int
    timestamp_s: float
    relative_s: float
    mac: str
    length: int
    pdu_type: str
    rssi_dbm: Optional[int]
    channel: Optional[int]
    duration_us: float
    interval_ms: Optional[float]
    decoded: DecodedEddystone
    raw_data: str
    service_data: str


def decode_eddystone(service_uuid: str, service_data: str, raw_data: str) -> Optional[DecodedEddystone]:
    combined = normalize_hex(service_uuid) + normalize_hex(service_data)
    combined = combined.replace('aafe', 'feaa', 1)
    index = combined.find('feaa')
    if index >= 0:
        payload = combined[index + 4:]
    else:
        raw = normalize_hex(raw_data)
        index = raw.find('aafe')
        if index < 0:
            index = raw.find('feaa')
        if index < 0:
            return None
        payload = raw[index + 4:]

    if len(payload) < 4:
        return None
    frame_type = payload[:2]
    tx_power = decode_signed_byte(payload[2:4])

    if frame_type == '00' and len(payload) >= 40:
        namespace = payload[4:24]
        instance = payload[24:36]
        return DecodedEddystone('Eddystone-UID', f'{namespace}/{instance}', tx_power, namespace, instance)

    if frame_type == '10' and len(payload) >= 6:
        scheme_codes = {0: 'http://www.', 1: 'https://www.', 2: 'http://', 3: 'https://'}
        expansion_codes = {value: key for key, value in URL_EXPANSIONS.items()}
        data = bytes.fromhex(payload[4:])
        if not data:
            return None
        parts = [scheme_codes.get(data[0], '')]
        for byte in data[1:]:
            if byte in expansion_codes:
                parts.append(expansion_codes[byte])
            elif 32 <= byte <= 126:
                parts.append(chr(byte))
        url = ''.join(parts)
        return DecodedEddystone('Eddystone-URL', url, tx_power, url=url)

    return None


class BLECapture:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.csv_path = output_dir / 'trames_eddystone.csv'
        self.process: Optional[subprocess.Popen[str]] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.queue: queue.Queue[BLEFrame] = queue.Queue()
        self.interface_name: Optional[str] = None
        self.first_timestamp: Optional[float] = None
        self.last_timestamp: Optional[float] = None
        self.count = 0

    @staticmethod
    def find_sniffer() -> Optional[str]:
        try:
            result = subprocess.run(['tshark', '-D'], check=True, capture_output=True, text=True)
        except Exception:
            return None
        preferred, fallback = [], []
        for line in result.stdout.splitlines():
            match = re.match(r'^\d+\.\s+(.+?)(?:\s+\(|$)', line.strip())
            if not match:
                continue
            iface = match.group(1).strip()
            lower = line.lower()
            if 'nrf sniffer' in lower or 'nrf_sniffer' in lower:
                preferred.append(iface)
            elif '/dev/ttyusb' in lower or '/dev/ttyacm' in lower:
                fallback.append(iface)
        return preferred[0] if preferred else (fallback[0] if fallback else None)

    def start(self) -> None:
        self.interface_name = self.find_sniffer()
        if not self.interface_name:
            raise RuntimeError('Aucun nRF Sniffer détecté. Vérifie avec : tshark -D')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open('w', newline='', encoding='utf-8') as file:
            csv.writer(file, delimiter=';').writerow([
                'numero', 'timestamp_s', 'temps_relatif_s', 'mac', 'type', 'identifiant',
                'namespace', 'instance', 'url', 'tx_power_dbm', 'rssi_dbm', 'canal',
                'longueur', 'duree_estimee_us', 'intervalle_paquets_ms', 'type_pdu',
                'donnees_brutes', 'service_data'
            ])

        command = ['tshark', '-l', '-n', '-i', self.interface_name, '-Y', 'btle', '-T', 'fields',
                   '-E', 'separator=;', '-E', 'occurrence=f', '-E', 'quote=n']
        for field in TSHARK_FIELDS:
            command.extend(['-e', field])
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.running = True
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
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

    def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            if not self.running:
                break
            parts = line.strip().split(';')
            while len(parts) < len(TSHARK_FIELDS):
                parts.append('')
            (time_epoch, mac, length, pdu_type, rssi, channel, _company_id,
             raw_data, service_uuid, service_data) = parts[:len(TSHARK_FIELDS)]
            if not time_epoch or not mac:
                continue
            decoded = decode_eddystone(service_uuid, service_data, raw_data)
            if not decoded:
                continue
            try:
                timestamp = float(time_epoch)
            except ValueError:
                continue
            if self.first_timestamp is None:
                self.first_timestamp = timestamp
            interval = None if self.last_timestamp is None else (timestamp - self.last_timestamp) * 1000
            self.last_timestamp = timestamp
            self.count += 1
            length_value = safe_int(length) or 0
            frame = BLEFrame(
                self.count, timestamp, timestamp - self.first_timestamp, mac, length_value,
                pdu_type or 'Inconnu', safe_int(rssi), safe_int(channel),
                duration_le1m_us(length_value), interval, decoded, raw_data, service_data
            )
            with self.csv_path.open('a', newline='', encoding='utf-8') as file:
                csv.writer(file, delimiter=';').writerow([
                    frame.number, f'{frame.timestamp_s:.9f}', f'{frame.relative_s:.6f}', frame.mac,
                    decoded.frame_type, decoded.identifier, decoded.namespace, decoded.instance,
                    decoded.url, decoded.tx_power_dbm,
                    '' if frame.rssi_dbm is None else frame.rssi_dbm,
                    '' if frame.channel is None else frame.channel,
                    frame.length, f'{frame.duration_us:.3f}',
                    '' if frame.interval_ms is None else f'{frame.interval_ms:.3f}',
                    frame.pdu_type, frame.raw_data, frame.service_data
                ])
            self.queue.put(frame)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title('Eddystone Studio - Raspberry Pi + nRF51822')
        self.root.geometry('1500x900')

        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.output_dir = OUTPUT_ROOT / f'acquisition_{stamp}'
        self.emitter = EddystoneEmitter()
        self.capture = BLECapture(self.output_dir)
        self.frames: list[BLEFrame] = []
        self.capture_running = False

        self.mode_var = tk.StringVar(value='Eddystone-UID')
        self.namespace_var = tk.StringVar(value='0102030405060708090a')
        self.instance_var = tk.StringVar(value='111213141516')
        self.url_var = tk.StringVar(value='https://www.example.com')
        self.tx_var = tk.StringVar(value='-59')
        self.interval_var = tk.StringVar(value='100')

        self._build_ui()
        self._update_fields()
        self.root.after(REFRESH_MS, self.update_ui)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=8, pady=8)
        self.emit_tab = ttk.Frame(notebook)
        self.analyze_tab = ttk.Frame(notebook)
        notebook.add(self.emit_tab, text='Émission Eddystone')
        notebook.add(self.analyze_tab, text='Analyse nRF Sniffer')
        self._build_emitter_tab()
        self._build_analyzer_tab()

    def _build_emitter_tab(self) -> None:
        form = ttk.LabelFrame(self.emit_tab, text='Configuration', padding=15)
        form.pack(fill='x', padx=12, pady=12)
        ttk.Label(form, text='Type :').grid(row=0, column=0, sticky='w', padx=5, pady=6)
        self.mode_combo = ttk.Combobox(form, textvariable=self.mode_var,
            values=['Eddystone-UID', 'Eddystone-URL'], state='readonly', width=22)
        self.mode_combo.grid(row=0, column=1, sticky='w', padx=5, pady=6)
        self.mode_combo.bind('<<ComboboxSelected>>', lambda _e: self._update_fields())

        ttk.Label(form, text='Namespace (10 octets) :').grid(row=1, column=0, sticky='w', padx=5, pady=6)
        self.namespace_entry = ttk.Entry(form, textvariable=self.namespace_var, width=30)
        self.namespace_entry.grid(row=1, column=1, sticky='w', padx=5, pady=6)
        ttk.Label(form, text='Instance (6 octets) :').grid(row=1, column=2, sticky='w', padx=5, pady=6)
        self.instance_entry = ttk.Entry(form, textvariable=self.instance_var, width=22)
        self.instance_entry.grid(row=1, column=3, sticky='w', padx=5, pady=6)

        ttk.Label(form, text='URL :').grid(row=2, column=0, sticky='w', padx=5, pady=6)
        self.url_entry = ttk.Entry(form, textvariable=self.url_var, width=60)
        self.url_entry.grid(row=2, column=1, columnspan=3, sticky='w', padx=5, pady=6)

        ttk.Label(form, text='Tx Power de référence :').grid(row=3, column=0, sticky='w', padx=5, pady=6)
        ttk.Entry(form, textvariable=self.tx_var, width=12).grid(row=3, column=1, sticky='w', padx=5, pady=6)
        ttk.Label(form, text='Intervalle (ms) :').grid(row=3, column=2, sticky='w', padx=5, pady=6)
        ttk.Entry(form, textvariable=self.interval_var, width=12).grid(row=3, column=3, sticky='w', padx=5, pady=6)

        controls = ttk.Frame(self.emit_tab, padding=12)
        controls.pack(fill='x')
        self.emit_start = ttk.Button(controls, text='Démarrer l’émission', command=self.start_emission)
        self.emit_start.pack(side='left', padx=5)
        self.emit_stop = ttk.Button(controls, text='Arrêter l’émission', command=self.stop_emission, state='disabled')
        self.emit_stop.pack(side='left', padx=5)
        self.emit_status = ttk.Label(controls, text='Émetteur arrêté')
        self.emit_status.pack(side='left', padx=20)

    def _update_fields(self) -> None:
        uid = self.mode_var.get() == 'Eddystone-UID'
        self.namespace_entry.configure(state='normal' if uid else 'disabled')
        self.instance_entry.configure(state='normal' if uid else 'disabled')
        self.url_entry.configure(state='disabled' if uid else 'normal')

    def start_emission(self) -> None:
        try:
            tx = int(self.tx_var.get())
            interval = int(self.interval_var.get())
            if self.mode_var.get() == 'Eddystone-UID':
                self.emitter.start_uid(self.namespace_var.get(), self.instance_var.get(), tx, interval)
            else:
                self.emitter.start_url(self.url_var.get(), tx, interval)
        except Exception as error:
            messagebox.showerror('Erreur d’émission', str(error))
            return
        self.emit_start.configure(state='disabled')
        self.emit_stop.configure(state='normal')
        self.mode_combo.configure(state='disabled')
        self.emit_status.configure(text=f'Émission active : {self.emitter.mode}')

    def stop_emission(self) -> None:
        self.emitter.stop()
        self.emit_start.configure(state='normal')
        self.emit_stop.configure(state='disabled')
        self.mode_combo.configure(state='readonly')
        self._update_fields()
        self.emit_status.configure(text='Émetteur arrêté')

    def _build_analyzer_tab(self) -> None:
        controls = ttk.Frame(self.analyze_tab, padding=8)
        controls.pack(fill='x')
        self.capture_start = ttk.Button(controls, text='Démarrer acquisition', command=self.start_capture)
        self.capture_start.pack(side='left', padx=5)
        self.capture_stop = ttk.Button(controls, text='Arrêter et sauvegarder', command=self.stop_capture, state='disabled')
        self.capture_stop.pack(side='left', padx=5)
        self.capture_status = ttk.Label(controls, text='Acquisition arrêtée')
        self.capture_status.pack(side='left', padx=20)

        stats = ttk.LabelFrame(self.analyze_tab, text='Statistiques', padding=8)
        stats.pack(fill='x', padx=8)
        self.stat_vars = {
            'frames': tk.StringVar(value='Trames : 0'),
            'type': tk.StringVar(value='Type : --'),
            'rssi': tk.StringVar(value='RSSI : --'),
            'duration': tk.StringVar(value='Durée : --'),
            'interval': tk.StringVar(value='Intervalle : --'),
            'channel': tk.StringVar(value='Canal : --'),
        }
        for var in self.stat_vars.values():
            ttk.Label(stats, textvariable=var, font=('TkDefaultFont', 10, 'bold')).pack(side='left', padx=15)

        tabs = ttk.Notebook(self.analyze_tab)
        tabs.pack(fill='both', expand=True, padx=8, pady=8)
        live = ttk.Frame(tabs); dist = ttk.Frame(tabs); timeline = ttk.Frame(tabs); table = ttk.Frame(tabs)
        tabs.add(live, text='RSSI et durée'); tabs.add(dist, text='Histogrammes'); tabs.add(timeline, text='Chronologie'); tabs.add(table, text='Trames')

        self.live_fig = Figure(figsize=(11, 7), dpi=100)
        self.ax_rssi = self.live_fig.add_subplot(211)
        self.ax_duration = self.live_fig.add_subplot(212)
        self.live_canvas = FigureCanvasTkAgg(self.live_fig, master=live)
        self.live_canvas.get_tk_widget().pack(fill='both', expand=True)

        self.dist_fig = Figure(figsize=(11, 7), dpi=100)
        self.ax_hist = self.dist_fig.add_subplot(211)
        self.ax_channels = self.dist_fig.add_subplot(212)
        self.dist_canvas = FigureCanvasTkAgg(self.dist_fig, master=dist)
        self.dist_canvas.get_tk_widget().pack(fill='both', expand=True)

        self.timeline_fig = Figure(figsize=(11, 7), dpi=100)
        self.ax_timeline = self.timeline_fig.add_subplot(111)
        self.timeline_canvas = FigureCanvasTkAgg(self.timeline_fig, master=timeline)
        self.timeline_canvas.get_tk_widget().pack(fill='both', expand=True)

        columns = ('n', 'time', 'mac', 'type', 'id', 'rssi', 'channel', 'length', 'duration', 'interval', 'tx')
        self.tree = ttk.Treeview(table, columns=columns, show='headings')
        labels = {'n':'N°','time':'Temps','mac':'MAC','type':'Type','id':'Identifiant / URL','rssi':'RSSI','channel':'Canal','length':'Longueur','duration':'Durée µs','interval':'Intervalle ms','tx':'Tx Power'}
        widths = {'n':50,'time':85,'mac':145,'type':120,'id':430,'rssi':70,'channel':60,'length':75,'duration':85,'interval':105,'tx':75}
        for col in columns:
            self.tree.heading(col, text=labels[col]); self.tree.column(col, width=widths[col], anchor='center')
        sy = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        sx = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.pack(side='top', fill='both', expand=True); sy.pack(side='right', fill='y'); sx.pack(side='bottom', fill='x')

    def start_capture(self) -> None:
        try:
            self.capture.start()
        except Exception as error:
            messagebox.showerror('Erreur de capture', str(error))
            return
        self.capture_running = True
        self.capture_start.configure(state='disabled')
        self.capture_stop.configure(state='normal')
        self.capture_status.configure(text=f'Capture sur {self.capture.interface_name}')

    def stop_capture(self) -> None:
        if not self.capture_running:
            return
        self.capture.stop(); self.capture_running = False
        while True:
            try:
                frame = self.capture.queue.get_nowait()
            except queue.Empty:
                break
            self.frames.append(frame); self._insert_row(frame)
        if self.frames:
            self._update_stats(); self._update_plots(); self.root.update_idletasks(); self._save_graphs()
        self.capture_start.configure(state='normal'); self.capture_stop.configure(state='disabled')
        self.capture_status.configure(text=f'Sauvegardé dans {self.output_dir}')
        messagebox.showinfo('Acquisition sauvegardée', f'CSV : {self.capture.csv_path}\nGraphes : {self.output_dir}')

    def update_ui(self) -> None:
        new = False
        while True:
            try:
                frame = self.capture.queue.get_nowait()
            except queue.Empty:
                break
            self.frames.append(frame); self._insert_row(frame); new = True
        if new:
            self._update_stats(); self._update_plots()
        self.root.after(REFRESH_MS, self.update_ui)

    def _insert_row(self, frame: BLEFrame) -> None:
        self.tree.insert('', 0, values=(
            frame.number, f'{frame.relative_s:.3f}', frame.mac, frame.decoded.frame_type,
            frame.decoded.identifier, '--' if frame.rssi_dbm is None else frame.rssi_dbm,
            '--' if frame.channel is None else frame.channel, frame.length, f'{frame.duration_us:.1f}',
            '--' if frame.interval_ms is None else f'{frame.interval_ms:.2f}', frame.decoded.tx_power_dbm
        ))
        children = self.tree.get_children()
        if len(children) > 600:
            self.tree.delete(children[-1])

    def _update_stats(self) -> None:
        frame = self.frames[-1]
        rssis = [f.rssi_dbm for f in self.frames if f.rssi_dbm is not None]
        intervals = [f.interval_ms for f in self.frames if f.interval_ms is not None]
        self.stat_vars['frames'].set(f'Trames : {len(self.frames)}')
        self.stat_vars['type'].set(f'Type : {frame.decoded.frame_type}')
        self.stat_vars['rssi'].set('RSSI : --' if not rssis else f'RSSI : {rssis[-1]} dBm | moy. {sum(rssis)/len(rssis):.1f}')
        self.stat_vars['duration'].set(f'Durée : {frame.duration_us:.1f} µs')
        self.stat_vars['interval'].set('Intervalle : --' if not intervals else f'Intervalle : {intervals[-1]:.1f} ms | moy. {sum(intervals)/len(intervals):.1f}')
        self.stat_vars['channel'].set('Canal : --' if frame.channel is None else f'Canal : {frame.channel}')

    def _update_plots(self) -> None:
        data = self.frames[-MAX_POINTS:]
        times = [f.relative_s for f in data]
        rssis = [f.rssi_dbm for f in data if f.rssi_dbm is not None]
        rssi_times = [f.relative_s for f in data if f.rssi_dbm is not None]
        durations = [f.duration_us for f in data]

        self.ax_rssi.clear(); self.ax_rssi.set_title('RSSI en fonction du temps'); self.ax_rssi.set_xlabel('Temps relatif (s)'); self.ax_rssi.set_ylabel('RSSI (dBm)'); self.ax_rssi.grid(True)
        if rssis: self.ax_rssi.plot(rssi_times, rssis, marker='o', markersize=3)
        self.ax_duration.clear(); self.ax_duration.set_title('Durée estimée des trames'); self.ax_duration.set_xlabel('Temps relatif (s)'); self.ax_duration.set_ylabel('Durée (µs)'); self.ax_duration.grid(True)
        if durations: self.ax_duration.plot(times, durations, marker='o', markersize=3)
        self.live_fig.tight_layout(); self.live_canvas.draw_idle()

        self.ax_hist.clear(); self.ax_hist.set_title('Histogramme RSSI'); self.ax_hist.set_xlabel('RSSI (dBm)'); self.ax_hist.set_ylabel('Nombre de trames')
        if rssis: self.ax_hist.hist(rssis, bins=min(20, max(5, len(set(rssis)))))
        counts = {37:0, 38:0, 39:0}
        for f in self.frames:
            if f.channel in counts: counts[f.channel] += 1
        self.ax_channels.clear(); self.ax_channels.set_title('Répartition des canaux'); self.ax_channels.set_xlabel('Canal'); self.ax_channels.set_ylabel('Nombre de trames'); self.ax_channels.bar(list(counts.keys()), list(counts.values())); self.ax_channels.set_xticks([37,38,39])
        self.dist_fig.tight_layout(); self.dist_canvas.draw_idle()

        self.ax_timeline.clear(); self.ax_timeline.set_title('Chronologie des trames Eddystone'); self.ax_timeline.set_xlabel('Temps relatif (s)'); self.ax_timeline.set_ylabel('Numéro de trame'); self.ax_timeline.grid(True)
        if data: self.ax_timeline.scatter([f.relative_s for f in data], [f.number for f in data], s=15)
        self.timeline_fig.tight_layout(); self.timeline_canvas.draw_idle()

    def _save_graphs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.live_fig.savefig(self.output_dir / 'rssi_et_duree.png', dpi=200, bbox_inches='tight')
        self.dist_fig.savefig(self.output_dir / 'histogramme_rssi_et_canaux.png', dpi=200, bbox_inches='tight')
        self.timeline_fig.savefig(self.output_dir / 'chronologie_trames.png', dpi=200, bbox_inches='tight')

    def on_close(self) -> None:
        if self.capture_running:
            self.stop_capture()
        if self.emitter.running:
            self.emitter.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
