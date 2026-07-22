from __future__ import annotations

from dataclasses import dataclass, asdict
import shutil
import subprocess
import threading
from typing import Callable, Optional

from ibeacon import decode_ibeacon

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
    manufacturer_hex: str

    def as_dict(self):
        return asdict(self)

def to_float(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", "."))
    except Exception:
        return None

def to_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None

class NRFIBeaconCapture:
    """
    Capture les trames BLE avec tshark puis ne transmet à l'interface
    que les trames iBeacon valides.
    """

    FIELDS = [
        "frame.time_epoch",
        "btle.advertising_address",
        "btle.advertising_header.pdu_type",
        "btle.length",
        "nordic_ble.rssi",
        "nordic_ble.channel",
        "btcommon.eir_ad.entry.data",
        "btcommon.eir_ad.entry.manufacturer_company_id",
    ]

    def __init__(
        self,
        interface: str,
        on_frame: Callable[[IBeaconFrame], None],
        on_log: Callable[[str], None],
    ):
        self.interface = interface
        self.on_frame = on_frame
        self.on_log = on_log
        self.process = None
        self.stop_event = threading.Event()

    def command(self):
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
            "aggregator=,",
            "-E",
            "quote=n",
        ]

        for field in self.FIELDS:
            command.extend(["-e", field])

        return command

    def start(self):
        if self.process is not None:
            raise RuntimeError("Une capture est déjà active.")

        if shutil.which("tshark") is None:
            raise RuntimeError("tshark est introuvable.")

        if not self.interface.strip():
            raise ValueError("L'interface nRF Sniffer est vide.")

        self.stop_event.clear()

        self.process = subprocess.Popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        assert self.process is not None
        assert self.process.stdout is not None

        for line in self.process.stdout:
            if self.stop_event.is_set():
                break

            parts = line.rstrip("\n").split(";")
            parts += [""] * (len(self.FIELDS) - len(parts))

            timestamp = to_float(parts[0])
            if timestamp is None:
                continue

            # Plusieurs éléments AD peuvent être séparés par une virgule.
            ad_candidates = [item for item in parts[6].split(",") if item]

            # Ajout du Company ID lorsque tshark le fournit séparément.
            company_id = parts[7].strip().lower().replace("0x", "")
            if company_id:
                ad_candidates += [
                    company_id + candidate
                    for candidate in list(ad_candidates)
                ]

            decoded = None
            selected_hex = ""

            for candidate in ad_candidates:
                decoded = decode_ibeacon(candidate)
                if decoded is not None:
                    selected_hex = candidate
                    break

            if decoded is None:
                continue

            frame = IBeaconFrame(
                timestamp=timestamp,
                address=parts[1],
                pdu_type=parts[2],
                length=to_int(parts[3]),
                rssi=to_float(parts[4]),
                channel=to_int(parts[5]),
                uuid=decoded.uuid,
                major=decoded.major,
                minor=decoded.minor,
                tx_power=decoded.tx_power,
                manufacturer_hex=selected_hex,
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
