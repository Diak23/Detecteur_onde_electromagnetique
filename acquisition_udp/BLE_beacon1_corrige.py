#!/usr/bin/env python3
"""
Beacon BLE non connectable sur Raspberry Pi avec BlueZ et D-Bus.

Cette version corrige les principales causes possibles de l'erreur :

    org.bluez.Error.Failed: Failed to register advertisement

Modifications apportees :
    - nom local raccourci pour respecter la limite des 31 octets ;
    - suppression de IncludeTxPower pour reduire la taille de l'annonce ;
    - payload proprietaire coherent, sans faux en-tete iBeacon 0x02 0x15 ;
    - chemin D-Bus construit a partir de l'index ;
    - verification et activation de l'adaptateur Bluetooth ;
    - nettoyage plus robuste lors de l'arret ;
    - messages d'erreur plus explicites.

Prerequis :
    sudo apt update
    sudo apt install -y bluez python3-dbus python3-gi
    sudo systemctl enable --now bluetooth

Lancement :
    sudo python3 BLE_beacon1_corrige.py

Verification :
    - nRF Connect sur smartphone ;
    - nRF Sniffer / Wireshark ;
    - un autre appareil BLE en mode scan.
"""

import sys

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib


# ---------------------------------------------------------------------------
# Interfaces BlueZ / D-Bus
# ---------------------------------------------------------------------------

BLUEZ_SERVICE_NAME = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"


# ---------------------------------------------------------------------------
# Configuration du beacon
# ---------------------------------------------------------------------------

# 0xFFFF est reserve aux essais et au developpement.
# Pour Texas Instruments, la valeur correcte serait 0x000D.
COMPANY_ID = 0xFFFF

# Format proprietaire :
# [version][identifiant 4 octets][major 2 octets][minor 2 octets][puissance calibree]
#
# 0xC5 interprete en entier signe sur 8 bits correspond a -59 dBm.
MANUFACTURER_PAYLOAD = [
    0x01,                   # Version du format proprietaire
    0xDE, 0xAD, 0xBE, 0xEF, # Identifiant court
    0x00, 0x01,             # Major = 1
    0x00, 0x2A,             # Minor = 42
    0xC5,                   # Puissance calibree = -59 dBm
]

# Nom volontairement court pour ne pas depasser la taille maximale.
LOCAL_NAME = "TEMPO"

# "broadcast" correspond a un advertising non connectable.
ADVERTISEMENT_TYPE = "broadcast"


class InvalidArgsException(dbus.exceptions.DBusException):
    """Erreur renvoyee si BlueZ demande une mauvaise interface."""

    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class Advertisement(dbus.service.Object):
    """Objet D-Bus representant l'annonce BLE."""

    def __init__(self, bus, index, advertising_type):
        self.path = f"/org/bluez/example/advertisement{index}"
        self.bus = bus
        self.ad_type = advertising_type
        self.local_name = LOCAL_NAME
        self.manufacturer_data = {
            COMPANY_ID: MANUFACTURER_PAYLOAD
        }

        # Desactive pour eviter de depasser la limite legacy de 31 octets.
        self.include_tx_power = False

        super().__init__(bus, self.path)

    def get_properties(self):
        """Retourne toutes les proprietes de l'annonce a BlueZ."""

	properties = {
   	 "Type": dbus.String(self.ad_type),
   	 "ManufacturerData": dbus.Dictionary(
        {
            dbus.UInt16(company_id): dbus.Array(
                payload,
                signature="y",
            )
            for company_id, payload in self.manufacturer_data.items()
        },
        signature="qv",
    ),
}
        if self.include_tx_power:
            properties["IncludeTxPower"] = dbus.Boolean(True)

        return {
            LE_ADVERTISEMENT_IFACE: properties
        }

    def get_path(self):
        """Retourne le chemin D-Bus de l'objet advertisement."""

        return dbus.ObjectPath(self.path)

    @dbus.service.method(
        DBUS_PROP_IFACE,
        in_signature="s",
        out_signature="a{sv}",
    )
    def GetAll(self, interface):
        """Methode appelee par BlueZ pour lire les proprietes."""

        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(
        LE_ADVERTISEMENT_IFACE,
        in_signature="",
        out_signature="",
    )
    def Release(self):
        """Methode appelee lorsque BlueZ libere l'annonce."""

        print("Advertisement liberee par BlueZ.")


def get_managed_objects(bus):
    """Recupere tous les objets geres par BlueZ."""

    object_manager = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, "/"),
        DBUS_OM_IFACE,
    )
    return object_manager.GetManagedObjects()


def find_adapter(bus):
    """
    Cherche le premier adaptateur qui expose
    org.bluez.LEAdvertisingManager1.
    """

    objects = get_managed_objects(bus)

    for path, interfaces in objects.items():
        if LE_ADVERTISING_MANAGER_IFACE in interfaces:
            return path

    return None


def power_on_adapter(bus, adapter_path):
    """Active l'adaptateur Bluetooth s'il est eteint."""

    adapter_object = bus.get_object(
        BLUEZ_SERVICE_NAME,
        adapter_path,
    )

    properties = dbus.Interface(
        adapter_object,
        DBUS_PROP_IFACE,
    )

    powered = properties.Get(
        ADAPTER_IFACE,
        "Powered",
    )

    if not bool(powered):
        print("Activation de l'adaptateur Bluetooth...")
        properties.Set(
            ADAPTER_IFACE,
            "Powered",
            dbus.Boolean(True),
        )


def estimate_advertising_data_size():
    """
    Estimation de la taille des donnees d'advertising legacy.

    ManufacturerData :
        1 octet longueur
        1 octet type 0xFF
        2 octets Company ID
        N octets payload

    LocalName :
        1 octet longueur
        1 octet type
        N octets caracteres
    """

    manufacturer_size = 1 + 1 + 2 + len(MANUFACTURER_PAYLOAD)
    local_name_size = 1 + 1 + len(LOCAL_NAME.encode("utf-8"))

    return manufacturer_size + local_name_size


def register_ad_cb():
    """Callback appele lorsque BlueZ accepte l'annonce."""

    payload_hex = " ".join(
        f"{value:02X}" for value in MANUFACTURER_PAYLOAD
    )

    print()
    print("Beacon BLE actif.")
    print(f"Nom local       : {LOCAL_NAME}")
    print("Type            : broadcast, non connectable")
    print(f"Company ID      : 0x{COMPANY_ID:04X}")
    print(f"Payload         : {payload_hex}")
    print(
        "Taille estimee : "
        f"{estimate_advertising_data_size()} octets sur 31"
    )
    print()
    print("Appuie sur Ctrl+C pour arreter.")


def register_ad_error_cb(error):
    """Callback appele si l'enregistrement echoue."""

    print()
    print("Erreur lors de l'enregistrement de l'advertisement :")
    print(error)
    print()
    print("Verifications conseillees :")
    print("  1. sudo systemctl restart bluetooth")
    print("  2. bluetoothctl -> advertise off -> quit")
    print("  3. sudo btmgmt info")
    print("  4. sudo journalctl -u bluetooth -n 50 --no-pager")
    print()

    if mainloop is not None:
        mainloop.quit()


def unregister_advertisement(ad_manager, advertisement):
    """Supprime proprement l'annonce, sans masquer l'erreur d'origine."""

    try:
        ad_manager.UnregisterAdvertisement(
            advertisement.get_path()
        )
        print("Advertisement desenregistree.")
    except dbus.exceptions.DBusException as error:
        error_name = error.get_dbus_name()

        if error_name != "org.bluez.Error.DoesNotExist":
            print(
                "Avertissement pendant le desenregistrement : "
                f"{error}"
            )

    try:
        advertisement.remove_from_connection()
    except Exception:
        pass


mainloop = None


def main():
    """Point d'entree principal."""

    global mainloop

    dbus.mainloop.glib.DBusGMainLoop(
        set_as_default=True
    )

    bus = dbus.SystemBus()

    adapter_path = find_adapter(bus)

    if adapter_path is None:
        raise RuntimeError(
            "Aucun adaptateur BLE compatible avec "
            "LEAdvertisingManager1 n'a ete trouve.\n"
            "Verifie : bluetoothctl show, sudo btmgmt info "
            "et sudo systemctl status bluetooth."
        )

    print(f"Adaptateur trouve : {adapter_path}")

    power_on_adapter(
        bus,
        adapter_path,
    )

    estimated_size = estimate_advertising_data_size()

    if estimated_size > 31:
        raise ValueError(
            "Les donnees d'advertising estimees depassent "
            f"31 octets : {estimated_size} octets."
        )

    adapter_object = bus.get_object(
        BLUEZ_SERVICE_NAME,
        adapter_path,
    )

    ad_manager = dbus.Interface(
        adapter_object,
        LE_ADVERTISING_MANAGER_IFACE,
    )

    advertisement = Advertisement(
        bus,
        index=0,
        advertising_type=ADVERTISEMENT_TYPE,
    )

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
        print("\nArret demande par l'utilisateur.")

    finally:
        unregister_advertisement(
            ad_manager,
            advertisement,
        )


if __name__ == "__main__":
    try:
        main()

    except dbus.exceptions.DBusException as error:
        print(f"Erreur D-Bus : {error}")
        sys.exit(1)

    except Exception as error:
        print(f"Erreur : {error}")
        sys.exit(1)
