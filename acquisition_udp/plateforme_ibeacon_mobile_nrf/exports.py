from __future__ import annotations

import csv
from datetime import datetime

def create_acquisition_folder(base_dir):
    folder = base_dir / (
        "acquisition_" +
        datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def write_dict_rows(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(rows[0].keys()),
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(rows)

def write_statistics(path, statistics):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(["indicateur", "valeur"])

        for key, value in statistics.items():
            writer.writerow([key, value])
