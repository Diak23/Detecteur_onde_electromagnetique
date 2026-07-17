"""
Beacon BLE non-connectable sur Raspberry Pi via BlueZ (D-Bus).

Equivalent Python du tableau advertData[] du document TI SWRA475A :
    - Flags (gérés automatiquement par BlueZ)
    - Manufacturer Specific Data (0xFF) avec un Company ID + payload libre
      (ici : un "beacon type", un UUID-like, Major/Minor, Power calibrée,
      exactement comme la structure iBeacon/propriétaire du document TI)

Prérequis (sur Raspberry Pi OS) :
    sudo apt update
    sudo apt install -y bluez python3-dbus python3-gi
    sudo systemctl restart bluetooth

Lancement (droits root nécessaires pour piloter l'adaptateur BLE) :
    sudo python3 ble_beacon.py

Vérification :
    - Avec un smartphone : app "nRF Connect" -> scanner -> repérer le Company ID
    - En ligne de commande : sudo hcitool lescan  (ou bluetoothctl -> scan on)
    - Avec un sniffer (nRF Sniffer, Wireshark) pour lire le paquet ADV_NONCONN_IND
"""

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

# ---------------------------------------------------------------------------
# Configuration du beacon — équivalent de advertData[] dans le document TI
# ---------------------------------------------------------------------------

BLUEZ_SERVICE_NAME = "org.bluez"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

ADAPTER_PATH = "/org/bluez/hci0"          # hci0 = adaptateur BLE (interne ou dongle USB)
ADVERTISEMENT_PATH = "/org/bluez/example/advertisement0"

# Company ID (format Bluetooth SIG). 0xFFFF = usage test/développement uniquement.
# Remplace par ton propre ID si tu en as un, ou par 0x0D00 (TI) à titre d'exemple pédagogique.
COMPANY_ID = 0xFFFF

# Payload personnalisé : structure librement inspirée du format "propriétaire"
# du document TI (section 5) : [beacon_type][uuid-like 4 octets][major][minor][power]
MANUFACTURER_PAYLOAD = [
    0x02, 0x15,             # "beacon type" (arbitraire, libre à définir)
    0xDE, 0xAD, 0xBE, 0xEF, # identifiant / UUID court (personnalisable)
    0x00, 0x01,             # Major
    0x00, 0x2A,             # Minor
    0xC5,                   # Power (2's complement de la Tx Power calibrée, ex: -59 dBm)
]

LOCAL_NAME = "TEMPO-BEACON"


class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class Advertisement(dbus.service.Object):
    """Objet D-Bus représentant l'annonce BLE (équivalent GAPRole_SetParameter côté TI)."""

    def __init__(self, bus, index, advertising_type):
        self.path = ADVERTISEMENT_PATH
        self.bus = bus
        self.ad_type = advertising_type  # "broadcast" = non-connectable
        self.local_name = LOCAL_NAME
        self.manufacturer_data = {COMPANY_ID: MANUFACTURER_PAYLOAD}
        self.include_tx_power = True
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        properties = {}
        properties["Type"] = self.ad_type
        properties["LocalName"] = dbus.String(self.local_name)
        properties["ManufacturerData"] = dbus.Dictionary(
            {
                dbus.UInt16(cid): dbus.Array(data, signature="y")
                for cid, data in self.manufacturer_data.items()
            },
            signature="qv",
        )
        properties["IncludeTxPower"] = dbus.Boolean(self.include_tx_power)
        return {LE_ADVERTISEMENT_IFACE: properties}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        print("Advertisement libérée par BlueZ")


def find_adapter(bus):
    remote_om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
    objects = remote_om.GetManagedObjects()
    for path, interfaces in objects.items():
        if LE_ADVERTISING_MANAGER_IFACE in interfaces:
            return path
    return None


def register_ad_cb():
    print(f"Beacon BLE actif : '{LOCAL_NAME}' (non-connectable, ADV_NONCONN_IND)")
    print(f"Company ID: 0x{COMPANY_ID:04X} | Payload: {['0x%02X' % b for b in MANUFACTURER_PAYLOAD]}")


def register_ad_error_cb(error):
    print(f"Erreur lors de l'enregistrement de l'advertisement : {error}")
    mainloop.quit()


def main():
    global mainloop

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    adapter_path = find_adapter(bus)
    if not adapter_path:
        raise RuntimeError(
            "Aucun adaptateur BLE compatible trouvé. "
            "Vérifie que le Bluetooth est activé (sudo hciconfig hci0 up) "
            "et que BlueZ est à jour (version >= 5.43 recommandée)."
        )

    ad_manager = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, adapter_path), LE_ADVERTISING_MANAGER_IFACE
    )

    advertisement = Advertisement(bus, 0, "broadcast")  # "broadcast" = non-connectable

    mainloop = GLib.MainLoop()

    ad_manager.RegisterAdvertisement(
        advertisement.get_path(),
        {},
        reply_handler=register_ad_cb,
        error_handler=register_ad_error_cb,
    )

    try:
        mainloop.run()
    except KeyboardInterrupt:
        print("\nArrêt du beacon...")
        ad_manager.UnregisterAdvertisement(advertisement.get_path())
        advertisement.remove_from_connection()


if __name__ == "__main__":
    main()
