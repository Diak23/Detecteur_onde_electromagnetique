from dataclasses import dataclass
import re
from typing import Optional
UUID_RE=re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
@dataclass(frozen=True)
class IBeacon:
    uuid:str; major:int; minor:int; tx_power:int

def validate(uuid,major,minor,tx_power):
    uuid=str(uuid).strip().lower(); major=int(major); minor=int(minor); tx_power=int(tx_power)
    if not UUID_RE.fullmatch(uuid): raise ValueError("UUID invalide : format 8-4-4-4-12 attendu.")
    if not 0<=major<=65535: raise ValueError("Major doit être entre 0 et 65535.")
    if not 0<=minor<=65535: raise ValueError("Minor doit être entre 0 et 65535.")
    if not -128<=tx_power<=127: raise ValueError("Tx Power doit être entre -128 et 127.")
    return IBeacon(uuid,major,minor,tx_power)

def manufacturer_payload(b):
    return b"\x02\x15"+bytes.fromhex(b.uuid.replace("-",""))+b.major.to_bytes(2,"big")+b.minor.to_bytes(2,"big")+bytes([b.tx_power&0xff])

def decode_manufacturer_hex(value)->Optional[IBeacon]:
    h=re.sub(r"[^0-9a-fA-F]","",value or "").lower()
    candidates=[]
    if h.startswith("4c00") or h.startswith("004c"): candidates.append(h[4:])
    candidates.append(h)
    for p in candidates:
        i=p.find("0215")
        if i<0: continue
        p=p[i:]
        if len(p)<50: continue
        try:
            raw=bytes.fromhex(p[:50]); x=raw[2:18].hex()
            uuid=f"{x[:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:]}"
            tp=raw[22]-256 if raw[22]>127 else raw[22]
            return IBeacon(uuid,int.from_bytes(raw[18:20],"big"),int.from_bytes(raw[20:22],"big"),tp)
        except Exception: pass
    return None
