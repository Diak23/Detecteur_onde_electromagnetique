import csv,json
from datetime import datetime
def new_folder(base):
    p=base/("acquisition_"+datetime.now().strftime("%Y%m%d_%H%M%S"));p.mkdir(parents=True,exist_ok=True);return p
def csv_rows(path,rows):
    if not rows:path.write_text("",encoding="utf-8");return
    with path.open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter=";");w.writeheader();w.writerows(rows)
def stats_csv(path,data):
    with path.open("w",newline="",encoding="utf-8") as f:w=csv.writer(f,delimiter=";");w.writerow(["indicateur","valeur"]);w.writerows(data.items())
def json_file(path,data):path.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")
