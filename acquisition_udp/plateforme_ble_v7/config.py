from pathlib import Path
APP_NAME="Plateforme BLE V7 — iBeacon / nRF Sniffer"
BASE_DIR=Path(__file__).resolve().parent
OUTPUT_DIR=BASE_DIR/"acquisitions_ble"
CALIBRATION_FILE=BASE_DIR/"calibration_rssi.json"
DEFAULT_UUID="e20a39f4-73f5-4bc4-a12f-17d1ad07a961"
DEFAULT_MAJOR=10
DEFAULT_MINOR=20
DEFAULT_TX_POWER=-56
DEFAULT_ADAPTER="hci0"
DEFAULT_SNIFFER="/dev/ttyUSB0-4.4"
DEFAULT_GROUP_WINDOW_MS=12.0
