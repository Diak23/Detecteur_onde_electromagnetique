from __future__ import annotations
import json, math, random, threading, time
from dataclasses import dataclass
from typing import Callable

@dataclass
class LoRaPacket:
    timestamp: float
    frequency_mhz: float
    rssi_dbm: float
    snr_db: float
    spreading_factor: int
    bandwidth_khz: float
    coding_rate: str
    payload_length: int
    crc_ok: bool
    payload_hex: str
    source: str

def time_on_air_seconds(length:int, sf:int, bw_khz:float, cr_den:int=5)->float:
    bw=bw_khz*1000.0
    de=1 if sf>=11 and bw_khz<=125 else 0
    cr=max(1,min(4,cr_den-4))
    ts=(2**sf)/bw
    preamble=(8+4.25)*ts
    numerator=8*length-4*sf+28+16
    denominator=4*(sf-2*de)
    payload_symbols=8+max(math.ceil(numerator/denominator)*(cr+4),0)
    return preamble+payload_symbols*ts

class LoRaSimulationDriver:
    def __init__(self,on_packet:Callable[[LoRaPacket],None],on_log:Callable[[str],None],interval_s=1.0,frequency_mhz=868.1,sf=7,bw_khz=125.0,cr='4/5'):
        self.on_packet=on_packet; self.on_log=on_log; self.interval_s=interval_s
        self.frequency_mhz=frequency_mhz; self.sf=sf; self.bw_khz=bw_khz; self.cr=cr
        self.stop_event=threading.Event()
    def start(self):
        self.stop_event.clear(); threading.Thread(target=self._loop,daemon=True).start(); self.on_log('Simulation LoRa démarrée.')
    def _loop(self):
        n=0
        while not self.stop_event.is_set():
            n+=1; payload=f'TEMPO_LORA_{n}'.encode()
            self.on_packet(LoRaPacket(time.time(),self.frequency_mhz,-88+random.gauss(0,3),7+random.gauss(0,1.2),self.sf,self.bw_khz,self.cr,len(payload),True,payload.hex(),'Simulation LoRa'))
            self.stop_event.wait(self.interval_s)
    def stop(self): self.stop_event.set()

class LoRaSerialJSONDriver:
    def __init__(self,port:str,baudrate:int,on_packet:Callable[[LoRaPacket],None],on_log:Callable[[str],None]):
        self.port=port; self.baudrate=baudrate; self.on_packet=on_packet; self.on_log=on_log; self.stop_event=threading.Event(); self.serial=None
    def start(self):
        try: import serial
        except ImportError as exc: raise RuntimeError('pyserial absent. Lancez ./install.sh') from exc
        self.serial=serial.Serial(self.port,self.baudrate,timeout=1); self.stop_event.clear(); threading.Thread(target=self._loop,daemon=True).start(); self.on_log(f'LoRa série ouvert sur {self.port}.')
    def _loop(self):
        while not self.stop_event.is_set():
            raw=self.serial.readline()
            if not raw: continue
            try:
                d=json.loads(raw.decode(errors='replace').strip())
                self.on_packet(LoRaPacket(float(d.get('timestamp',time.time())),float(d.get('frequency_mhz',868.1)),float(d['rssi_dbm']),float(d.get('snr_db',0)),int(d.get('sf',7)),float(d.get('bw_khz',125)),str(d.get('cr','4/5')),int(d.get('length',0)),bool(d.get('crc_ok',True)),str(d.get('payload_hex','')),'Module LoRa réel'))
            except Exception as exc: self.on_log(f'Ligne LoRa ignorée : {exc}')
    def stop(self):
        self.stop_event.set()
        if self.serial is not None: self.serial.close(); self.serial=None
