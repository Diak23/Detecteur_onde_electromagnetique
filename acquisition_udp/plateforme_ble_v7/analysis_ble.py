from dataclasses import dataclass,asdict
from statistics import mean,pstdev
from typing import Optional
@dataclass
class AdvertisingEvent:
    index:int; start_epoch:float; end_epoch:float; duration_ms:float; interval_ms:Optional[float]; packet_count:int; channels:str; rssi_mean:Optional[float]; address:str; uuid:str; major:Optional[int]; minor:Optional[int]
    def as_dict(self):return asdict(self)
def group_events(frames,window_ms=12.0):
    if not frames:return []
    groups=[]
    for fr in sorted(frames,key=lambda x:x.timestamp):
        key=(fr.address,fr.uuid,fr.major,fr.minor)
        if groups:
            last=groups[-1][-1]; lkey=(last.address,last.uuid,last.major,last.minor)
            if key==lkey and (fr.timestamp-last.timestamp)*1000<=window_ms:groups[-1].append(fr);continue
        groups.append([fr])
    prev={}; out=[]
    for n,g in enumerate(groups,1):
        a,b=g[0],g[-1]; key=(a.address,a.uuid,a.major,a.minor); inter=None if key not in prev else (a.timestamp-prev[key])*1000; prev[key]=a.timestamp
        vals=[x.rssi_calibrated for x in g if x.rssi_calibrated is not None]; ch=sorted({x.channel for x in g if x.channel is not None})
        out.append(AdvertisingEvent(n,a.timestamp,b.timestamp,(b.timestamp-a.timestamp)*1000,inter,len(g),",".join(map(str,ch)),mean(vals) if vals else None,a.address,a.uuid,a.major,a.minor))
    return out
def statistics(frames,events):
    r=[x.rssi_calibrated for x in frames if x.rssi_calibrated is not None]; inter=[x.interval_ms for x in events if x.interval_ms is not None]; dur=[x.duration_ms for x in events]
    return {"nombre_trames":len(frames),"nombre_evenements":len(events),"rssi_moyen_dbm":mean(r) if r else None,"rssi_ecart_type_db":pstdev(r) if len(r)>1 else (0.0 if r else None),"rssi_min_dbm":min(r) if r else None,"rssi_max_dbm":max(r) if r else None,"intervalle_moyen_ms":mean(inter) if inter else None,"duree_evenement_moyenne_ms":mean(dur) if dur else None,"canal_37":sum(x.channel==37 for x in frames),"canal_38":sum(x.channel==38 for x in frames),"canal_39":sum(x.channel==39 for x in frames)}
