from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

@dataclass(frozen=True)
class IBeaconData:
    company_id: int
    uuid: str
    major: int
    minor: int
    tx_power: int
    raw_hex: str

def normalize_hex(value: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", value or "").lower()

def signed_int8(value: int) -> int:
    return value - 256 if value > 127 else value

def format_uuid(raw: bytes) -> str:
    h = raw.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

def decode_ibeacon(value: str) -> Optional[IBeaconData]:
    """
    Détecte une trame iBeacon à partir d'une chaîne hexadécimale.

    Signatures acceptées :
    - 4c000215...
    - 004c0215...
    - ...0215... lorsque le champ tshark ne contient pas le Company ID
    """
    h = normalize_hex(value)
    if len(h) < 50:
        return None

    candidates = []

    for marker in ("4c000215", "004c0215"):
        start = h.find(marker)
        if start >= 0:
            candidates.append((start, marker))

    # Cas où tshark retire le Company ID.
    start_0215 = h.find("0215")
    if start_0215 >= 0:
        candidates.append((start_0215, "0215"))

    for start, marker in candidates:
        try:
            if marker in ("4c000215", "004c0215"):
                payload = h[start:start + 54]
                if len(payload) < 54:
                    continue

                if marker == "4c000215":
                    company_id = 0x004C
                    body = payload[4:]
                else:
                    company_id = 0x004C
                    body = payload[4:]
            else:
                body = h[start:start + 50]
                company_id = 0x004C

            if not body.startswith("0215") or len(body) < 50:
                continue

            raw = bytes.fromhex(body[:50])

            return IBeaconData(
                company_id=company_id,
                uuid=format_uuid(raw[2:18]),
                major=int.from_bytes(raw[18:20], "big"),
                minor=int.from_bytes(raw[20:22], "big"),
                tx_power=signed_int8(raw[22]),
                raw_hex=h,
            )
        except (ValueError, IndexError):
            continue

    return None
