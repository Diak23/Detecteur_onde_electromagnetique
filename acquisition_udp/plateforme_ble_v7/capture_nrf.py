from dataclasses import dataclass,asdict
import shutil,subprocess,threading
from typing import Optional
from ibeacon import decode_manufacturer_hex
@dataclass
class BLEFrame:
    timestamp:float; address:str; length:Optional[int]; pdu_type:str; rssi_raw:Optional[float]; rssi_calibrated:Optional[float]; channel:Optional[int]; manufacturer_hex:str; uuid:str=""; major:Optional[int]=None; minor:Optional[int]=None; tx_power:Optional[int]=None
    def as_dict(self): return asdict(self)
def f(v):
    try:return float(v.replace(",","."))
    except:return None
def i(v):
    try:return int(v)
    except:return None
class NRFCapture:
    FIELDS=["frame.time_epoch","btle.advertising_address","btle.length","btle.advertising_header.pdu_type","nordic_ble.rssi","nordic_ble.channel","btcommon.eir_ad.entry.data"]
    def __init__(self,interface,on_frame,on_log,offset=0.0): self.interface=interface; self.on_frame=on_frame; self.on_log=on_log; self.offset=offset; self.process=None; self.stop_event=threading.Event()
    def start(self):
        if shutil.which("tshark") is None: raise RuntimeError("tshark introuvable")
        cmd=["tshark","-l","-n","-i",self.interface,"-Y","btle","-T","fields","-E","separator=;","-E","occurrence=f","-E","quote=n"]
        for x in self.FIELDS: cmd += ["-e",x]
        self.process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1); threading.Thread(target=self._read,daemon=True).start(); threading.Thread(target=self._err,daemon=True).start()
    def _read(self):
        for line in self.process.stdout:
            if self.stop_event.is_set():break
            p=line.rstrip("\n").split(";"); p += [""]*(len(self.FIELDS)-len(p)); ts=f(p[0])
            if ts is None: continue
            r=f(p[4]); d=decode_manufacturer_hex(p[6]); self.on_frame(BLEFrame(ts,p[1],i(p[2]),p[3],r,None if r is None else r+self.offset,i(p[5]),p[6],d.uuid if d else "",d.major if d else None,d.minor if d else None,d.tx_power if d else None))
    def _err(self):
        for line in self.process.stderr:
            if line.strip():self.on_log("tshark: "+line.strip())
    def stop(self):
        self.stop_event.set()
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill()
        self.process=None
