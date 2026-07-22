from pathlib import Path

APP_TITLE = "Détecteur iBeacon — nRF Connect Mobile vers Raspberry Pi"
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "acquisitions_ibeacon"

DEFAULT_INTERFACE = "/dev/ttyUSB0-4.4"
DEFAULT_GROUP_WINDOW_MS = 15.0
DEFAULT_TARGET_UUID = ""
DEFAULT_TARGET_MAJOR = ""
DEFAULT_TARGET_MINOR = ""
