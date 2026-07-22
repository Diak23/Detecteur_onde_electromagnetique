#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,signal,sys
import dbus,dbus.exceptions,dbus.mainloop.glib,dbus.service
from gi.repository import GLib
from ibeacon import manufacturer_payload,validate
BLUEZ="org.bluez"; OM="org.freedesktop.DBus.ObjectManager"; PROPS="org.freedesktop.DBus.Properties"; MGR="org.bluez.LEAdvertisingManager1"; ADV="org.bluez.LEAdvertisement1"; PATH="/com/projeteea/blev7/advertisement0"
class Advertisement(dbus.service.Object):
    def __init__(self,bus,beacon): super().__init__(bus,PATH); self.beacon=beacon
    def props(self):
        payload=manufacturer_payload(self.beacon)
        return {"Type":dbus.String("broadcast"),"ManufacturerData":dbus.Dictionary({dbus.UInt16(0x004C):dbus.Array([dbus.Byte(x) for x in payload],signature="y")},signature="qv")}
    @dbus.service.method(PROPS,in_signature="s",out_signature="a{sv}")
    def GetAll(self,interface):
        if interface!=ADV: raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs",f"Interface inconnue : {interface}")
        return self.props()
    @dbus.service.method(PROPS,in_signature="ss",out_signature="v")
    def Get(self,interface,prop):
        p=self.GetAll(interface)
        if prop not in p: raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs",f"Propriété inconnue : {prop}")
        return p[prop]
    @dbus.service.method(PROPS,in_signature="ssv",out_signature="")
    def Set(self,interface,prop,value): raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.PropertyReadOnly","Lecture seule")
    @dbus.service.method(ADV,in_signature="",out_signature="")
    def Release(self): print("RELEASED",flush=True)
def manager_path(bus,adapter):
    objects=dbus.Interface(bus.get_object(BLUEZ,"/"),OM).GetManagedObjects(); preferred=f"/org/bluez/{adapter}"
    if preferred in objects and MGR in objects[preferred]: return preferred
    for path,ifaces in objects.items():
        if MGR in ifaces:return str(path)
    raise RuntimeError("Aucun LEAdvertisingManager1 trouvé")
def active(bus,path): return int(dbus.Interface(bus.get_object(BLUEZ,path),PROPS).Get(MGR,"ActiveInstances"))
def main(args):
    beacon=validate(args.uuid,args.major,args.minor,args.tx_power)
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True); bus=dbus.SystemBus(); path=manager_path(bus,args.adapter)
    mgr=dbus.Interface(bus.get_object(BLUEZ,path),MGR); adv=Advertisement(bus,beacon); loop=GLib.MainLoop(); state={"registered":False,"failed":False}
    def stop(*_):
        if state["registered"]:
            try:mgr.UnregisterAdvertisement(dbus.ObjectPath(PATH))
            except Exception as e: print(f"UNREGISTER_WARNING {e}",file=sys.stderr,flush=True)
        loop.quit()
    def ok():
        state["registered"]=True
        print("REGISTERED "+json.dumps({"active_instances":active(bus,path),"uuid":beacon.uuid,"major":beacon.major,"minor":beacon.minor,"tx_power":beacon.tx_power}),flush=True)
    def fail(err):
        state["failed"]=True; name=err.get_dbus_name() if hasattr(err,"get_dbus_name") else type(err).__name__; msg=err.get_dbus_message() if hasattr(err,"get_dbus_message") else str(err)
        print(f"REGISTER_ERROR {name}: {msg}",file=sys.stderr,flush=True); loop.quit()
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    mgr.RegisterAdvertisement(dbus.ObjectPath(PATH),dbus.Dictionary({},signature="sv"),reply_handler=ok,error_handler=fail)
    def timeout():
        if not state["registered"] and not state["failed"]: print("REGISTER_ERROR Timeout",file=sys.stderr,flush=True); loop.quit()
        return False
    GLib.timeout_add_seconds(15,timeout); loop.run(); return 0 if state["registered"] and not state["failed"] else 1
if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--uuid",required=True); p.add_argument("--major",type=int,required=True); p.add_argument("--minor",type=int,required=True); p.add_argument("--tx-power",type=int,required=True); p.add_argument("--adapter",default="hci0")
    try: raise SystemExit(main(p.parse_args()))
    except Exception as e: print(f"FATAL {type(e).__name__}: {e}",file=sys.stderr,flush=True); raise SystemExit(1)
