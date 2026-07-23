from __future__ import annotations

import re
import subprocess


def read_wifi_rssi(interface: str = "wlan0") -> float:
    """Lit le RSSI de la liaison Wi-Fi courante."""
    result = subprocess.run(
        ["iw", "dev", interface, "link"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    match = re.search(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", result.stdout)
    if not match:
        raise RuntimeError(
            f"Aucun RSSI Wi-Fi trouvé sur {interface}. "
            "Vérifiez que l'interface est connectée."
        )

    return float(match.group(1))
