from __future__ import annotations

import csv
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class BLEFrame:
    timestamp_s: float
    mac: str
    rssi_dbm: Optional[int]
    channel: Optional[int]
    pdu_length_bytes: int
    phy: str
    duration_us: float
    end_timestamp_s: float
    pdu_type: str


def calculate_ble_duration_us(
    pdu_length_bytes: int,
    phy: str = "LE 1M"
) -> float:
    """
    Calcule le temps d'occupation radio d'une trame BLE.

    pdu_length_bytes représente la longueur du PDU BLE,
    en incluant l'en-tête PDU de 2 octets lorsqu'elle provient
    du champ btle.length de Wireshark.

    Pour LE 1M :
        préambule       1 octet
        access address  4 octets
        PDU             longueur variable
        CRC             3 octets
    """
    if pdu_length_bytes < 0:
        raise ValueError("La longueur PDU ne peut pas être négative.")

    radio_bytes = 1 + 4 + pdu_length_bytes + 3

    if phy == "LE 1M":
        return radio_bytes * 8.0

    if phy == "LE 2M":
        # Préambule de 2 octets en LE 2M.
        radio_bits = (2 + 4 + pdu_length_bytes + 3) * 8
        return radio_bits / 2.0

    raise ValueError(f"PHY non pris en charge : {phy}")


class BLESnifferDriver:
    def __init__(
        self,
        interface: str,
        csv_path: str = "trames_ble.csv",
        on_frame: Optional[Callable[[BLEFrame], None]] = None,
    ) -> None:
        self.interface = interface
        self.csv_path = Path(csv_path)
        self.on_frame = on_frame

        self._process: Optional[subprocess.Popen[str]] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.frames: queue.Queue[BLEFrame] = queue.Queue()

    def start(self) -> None:
        if self._running:
            return

        command = [
            "tshark",
            "-l",
            "-i", self.interface,
            "-Y", "btle",
            "-T", "fields",
            "-E", "separator=;",
            "-E", "occurrence=f",
            "-e", "frame.time_epoch",
            "-e", "btle.advertising_address",
            "-e", "btle.length",
            "-e", "btle.advertising_header.pdu_type",
            "-e", "nordic_ble.rssi",
            "-e", "nordic_ble.channel",
        ]

        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False

        if self._process is not None:
            self._process.terminate()

            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()

        self._process = None

    def _read_loop(self) -> None:
        if self._process is None or self._process.stdout is None:
            return

        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.csv_path.exists()

        with self.csv_path.open("a", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)

            if not file_exists:
                writer.writerow([
                    "timestamp_debut_s",
                    "timestamp_fin_s",
                    "mac",
                    "rssi_dbm",
                    "canal",
                    "longueur_pdu_octets",
                    "phy",
                    "duree_us",
                    "type_pdu",
                ])

            for line in self._process.stdout:
                if not self._running:
                    break

                frame = self._parse_line(line)

                if frame is None:
                    continue

                self.frames.put(frame)

                writer.writerow([
                    f"{frame.timestamp_s:.9f}",
                    f"{frame.end_timestamp_s:.9f}",
                    frame.mac,
                    frame.rssi_dbm if frame.rssi_dbm is not None else "",
                    frame.channel if frame.channel is not None else "",
                    frame.pdu_length_bytes,
                    frame.phy,
                    f"{frame.duration_us:.3f}",
                    frame.pdu_type,
                ])
                csv_file.flush()

                if self.on_frame is not None:
                    self.on_frame(frame)

    @staticmethod
    def _optional_int(value: str) -> Optional[int]:
        value = value.strip()

        if not value:
            return None

        match = re.search(r"-?\d+", value)
        return int(match.group()) if match else None

    def _parse_line(self, line: str) -> Optional[BLEFrame]:
        fields = line.rstrip("\n").split(";")

        while len(fields) < 6:
            fields.append("")

        timestamp_text = fields[0].strip()
        mac = fields[1].strip() or "inconnue"
        length_text = fields[2].strip()
        pdu_type = fields[3].strip() or "inconnu"
        rssi_text = fields[4].strip()
        channel_text = fields[5].strip()

        if not timestamp_text or not length_text:
            return None

        try:
            timestamp_s = float(timestamp_text)
            pdu_length = int(length_text)
        except ValueError:
            return None

        phy = "LE 1M"
        duration_us = calculate_ble_duration_us(pdu_length, phy)
        end_timestamp_s = timestamp_s + duration_us / 1_000_000.0

        return BLEFrame(
            timestamp_s=timestamp_s,
            mac=mac,
            rssi_dbm=self._optional_int(rssi_text),
            channel=self._optional_int(channel_text),
            pdu_length_bytes=pdu_length,
            phy=phy,
            duration_us=duration_us,
            end_timestamp_s=end_timestamp_s,
            pdu_type=pdu_type,
        )


def display_frame(frame: BLEFrame) -> None:
    print(
        f"BLE | MAC={frame.mac} | "
        f"RSSI={frame.rssi_dbm} dBm | "
        f"canal={frame.channel} | "
        f"PDU={frame.pdu_length_bytes} octets | "
        f"durée={frame.duration_us:.1f} µs"
    )


if __name__ == "__main__":
    # Remplacer par le nom ou numéro affiché par `tshark -D`.
    NRF_INTERFACE = "/dev/ttyUSB0-4.4"

    driver = BLESnifferDriver(
        interface=NRF_INTERFACE,
        csv_path="acquisitions_ble/trames_ble.csv",
        on_frame=display_frame,
    )

    try:
        driver.start()
        print("Capture BLE démarrée. Ctrl+C pour arrêter.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nArrêt de la capture.")

    finally:
        driver.stop()
