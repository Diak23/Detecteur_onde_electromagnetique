from __future__ import annotations

import queue
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class BLEFrame:
    timestamp: float
    address: str
    rssi_dbm: Optional[float]
    channel: Optional[int]
    length_bytes: Optional[int]
    pdu_type: str
    manufacturer: str
    device_type: str
    raw_hex: str


def _parse_float(value: str):
    try:
        return float(value.replace(",", "."))
    except Exception:
        return None


def _parse_int(value: str):
    try:
        return int(value, 0)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return None


def _clean_hex(value: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", value or "").lower()


def classify_ble_payload(values: list[str]) -> tuple[str, str, str]:
    joined_text = " | ".join(values).lower()
    joined_hex = "".join(_clean_hex(v) for v in values)

    if "airpods" in joined_text:
        return "Apple", "AirPods", joined_hex[:512]

    apple = ("4c00" in joined_hex) or ("004c" in joined_hex) or ("apple" in joined_text)
    probable_audio = any(sig in joined_hex for sig in ("0719", "1910", "0f05"))

    if apple and probable_audio:
        return "Apple", "AirPods / accessoire audio Apple probable", joined_hex[:512]
    if apple:
        return "Apple", "Appareil Apple BLE", joined_hex[:512]

    if "0215" in joined_hex:
        return "Apple", "iBeacon", joined_hex[:512]

    return "Inconnu", "Appareil BLE", joined_hex[:512]


class BLETsharkCapture:
    META_FIELDS = [
        "frame.time_epoch",
        "btle.advertising_address",
        "btle.length",
        "btle.advertising_header.pdu_type",
        "nordic_ble.rssi",
        "nordic_ble.channel",
    ]

    RAW_CANDIDATES = [
        "btcommon.eir_ad.entry.data",
        "btcommon.eir_ad.entry.service_data",
        "btcommon.eir_ad.entry.device_name",
        "btle.advertising_data",
        "btle.data",
        "data.data",
    ]

    def __init__(
        self,
        interface: str,
        on_frame: Callable[[BLEFrame], None],
        on_log: Callable[[str], None],
    ):
        self.interface = interface
        self.on_frame = on_frame
        self.on_log = on_log
        self.process = None
        self.stop_event = threading.Event()
        self.meta_fields = []
        self.raw_fields = []

    @staticmethod
    def available_fields() -> set[str]:
        result = subprocess.run(
            ["tshark", "-G", "fields"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        fields = set()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0] == "F":
                fields.add(parts[2])
        return fields

    @staticmethod
    def list_interfaces() -> list[str]:
        result = subprocess.run(
            ["tshark", "-D"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        output = []
        for line in result.stdout.splitlines():
            if ". " in line:
                output.append(line.split(". ", 1)[1].strip())
        return output

    def start(self) -> None:
        if shutil.which("tshark") is None:
            raise RuntimeError("tshark est introuvable.")

        available = self.available_fields()
        self.meta_fields = [f for f in self.META_FIELDS if f in available]
        self.raw_fields = [f for f in self.RAW_CANDIDATES if f in available]

        if "frame.time_epoch" not in self.meta_fields:
            raise RuntimeError("Le champ frame.time_epoch est indisponible.")
        if not self.raw_fields:
            raise RuntimeError("Aucun champ BLE brut compatible n'a été trouvé.")

        command = [
            "tshark", "-l", "-n", "-i", self.interface,
            "-Y", "btle", "-T", "fields",
            "-E", "separator=;",
            "-E", "occurrence=a",
            "-E", "aggregator=|",
            "-E", "quote=n",
        ]
        for field in self.meta_fields + self.raw_fields:
            command.extend(["-e", field])

        self.on_log("Commande BLE : " + " ".join(command))
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.stop_event.clear()

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None

        fields = self.meta_fields + self.raw_fields
        index = {name: i for i, name in enumerate(fields)}

        for line in self.process.stdout:
            if self.stop_event.is_set():
                break

            parts = line.rstrip("\n").split(";")
            parts += [""] * (len(fields) - len(parts))

            timestamp = _parse_float(parts[index["frame.time_epoch"]])
            if timestamp is None:
                continue

            raw_values = []
            for field in self.raw_fields:
                raw_values.extend(
                    value for value in parts[index[field]].split("|") if value.strip()
                )

            manufacturer, device_type, raw_hex = classify_ble_payload(raw_values)

            frame = BLEFrame(
                timestamp=timestamp,
                address=parts[index["btle.advertising_address"]]
                if "btle.advertising_address" in index else "",
                rssi_dbm=_parse_float(parts[index["nordic_ble.rssi"]])
                if "nordic_ble.rssi" in index else None,
                channel=_parse_int(parts[index["nordic_ble.channel"]])
                if "nordic_ble.channel" in index else None,
                length_bytes=_parse_int(parts[index["btle.length"]])
                if "btle.length" in index else None,
                pdu_type=parts[index["btle.advertising_header.pdu_type"]]
                if "btle.advertising_header.pdu_type" in index else "",
                manufacturer=manufacturer,
                device_type=device_type,
                raw_hex=raw_hex,
            )
            self.on_frame(frame)

    def _read_stderr(self) -> None:
        assert self.process is not None
        assert self.process.stderr is not None

        for line in self.process.stderr:
            if line.strip():
                self.on_log("tshark : " + line.strip())

    def stop(self) -> None:
        self.stop_event.set()
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process = None
