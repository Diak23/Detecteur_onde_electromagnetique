import json
from statistics import mean
def load(path):
    if not path.exists():return {"offset_db":0.0,"reference_dbm":-56.0}
    try:return json.loads(path.read_text(encoding="utf-8"))
    except:return {"offset_db":0.0,"reference_dbm":-56.0}
def calculate(samples,reference):
    if not samples:raise ValueError("Aucun RSSI brut disponible")
    m=mean(samples);return {"reference_dbm":float(reference),"measured_mean_dbm":m,"offset_db":float(reference)-m,"sample_count":len(samples)}
def save(path,data):path.write_text(json.dumps(data,indent=2),encoding="utf-8")
