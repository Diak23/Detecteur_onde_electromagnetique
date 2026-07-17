#!/usr/bin/env python3
"""Beacon BLE non connectable sur Raspberry Pi avec BlueZ et D-Bus."""

import sys
from typing import Optional

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib


BLUEZ_SERVICE_NAME = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"

COMPANY_ID = 0xFFFF

MANUFACTURER_PAYLOAD = [
    0x01,
    0xDE, 0xAD, 0xBE, 0xEF,
    0x00, 0x01,
    0x00, 0x2A,
    0xC5,
]

ADVERTISEMENT_TYPE = "broadcast"


class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class Advertisement(dbus.service.Object):
    def __init__(
        self,
        bus: dbus.SystemBus,
        index: int,
        advertising_type: str,
    ) -> None:
        self.path = f"/org/bluez/example/advertisement{index}"
        self.ad_type = advertising_type
        self.manufacturer_data = {
            COMPANY_ID: MANUFACTURER_PAYLOAD,
        }

        super().__init__(bus, self.path)

    def get_properties(self) -> dict:
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

        return {
            LE_ADVERTISEMENT_IFACE: properties,
        }

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self.path)

    @dbus.service.method(
        DBUS_PROP_IFACE,
        in_signature="s",
        out_signature="a{sv}",
    )
    def GetAll(self, interface: str) -> dict:
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()

        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(
        LE_ADVERTISEMENT_IFACE,
        in_signature="",
        out_signature="",
    )
    def Release(self) -> None:
        print("Advertisement liberee par BlueZ.")


def get_managed_objects(bus: dbus.SystemBus) -> dict:
    object_manager = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, "/"),
        DBUS_OM_IFACE,
    )
    return object_manager.GetManagedObjects()


def find_adapter(bus: dbus.SystemBus) -> Optional[str]:
    objects = get_managed_objects(bus)

    for path, interfaces in objects.items():
        if LE_ADVERTISING_MANAGER_IFACE in interfaces:
            return str(path)

    return None


def power_on_adapter(
    bus: dbus.SystemBus,
    adapter_path: str,
) -> None:
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


def estimate_advertising_data_size() -> int:
    return 1 + 1 + 2 + len(MANUFACTURER_PAYLOAD)


def register_ad_cb() -> None:
    global advertisement_registered

    advertisement_registered = True

    payload_hex = " ".join(
        f"{value:02X}" for value in MANUFACTURER_PAYLOAD
    )

    print()
    print("Beacon BLE actif.")
    print("Type            : broadcast, non connectable")
    print(f"Company ID      : 0x{COMPANY_ID:04X}")
    print(f"Payload         : {payload_hex}")
    print(
        "Taille estimee : "
        f"{estimate_advertising_data_size()} octets sur 31"
    )
    print()
    print("Le beacon n'a pas de nom local.")
    print("Dans nRF Connect, cherche le Company ID 0xFFFF.")
    print("Appuie sur Ctrl+C pour arreter.")


def register_ad_error_cb(error) -> None:
    print()
    print("Erreur lors de l'enregistrement de l'advertisement :")
    print(error)
    print()
    print("Diagnostic conseille :")
    print("  sudo journalctl -u bluetooth -n 50 --no-pager")
    print()

    if mainloop is not None:
        mainloop.quit()


def unregister_advertisement(
    ad_manager,
    advertisement: Advertisement,
) -> None:
    if advertisement_registered:
        try:
            ad_manager.UnregisterAdvertisement(
                advertisement.get_path()
            )
            print("Advertisement desenregistree.")

        except dbus.exceptions.DBusException as error:
            if error.get_dbus_name() != "org.bluez.Error.DoesNotExist":
                print(
                    "Avertissement pendant le desenregistrement : "
                    f"{error}"
                )

    try:
        advertisement.remove_from_connection()
    except Exception:
        pass


mainloop: Optional[GLib.MainLoop] = None
advertisement_registered = False


def main() -> None:
    global mainloop

    dbus.mainloop.glib.DBusGMainLoop(
        set_as_default=True
    )

    bus = dbus.SystemBus()
    adapter_path = find_adapter(bus)

    if adapter_path is None:
        raise RuntimeError(
            "Aucun adaptateur BLE compatible avec "
            "LEAdvertisingManager1 n'a ete trouve."
        )

    print(f"Adaptateur trouve : {adapter_path}")

    power_on_adapter(
        bus,
        adapter_path,
    )

    estimated_size = estimate_advertising_data_size()

    if estimated_size > 31:
        raise ValueError(
            "Les donnees d'advertising depassent 31 octets : "
            f"{estimated_size} octets."
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
