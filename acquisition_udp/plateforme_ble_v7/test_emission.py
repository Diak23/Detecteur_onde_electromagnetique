#!/usr/bin/env python3
import argparse,subprocess,sys,time
def active(adapter):
    out=subprocess.check_output(["busctl","get-property","org.bluez",f"/org/bluez/{adapter}","org.bluez.LEAdvertisingManager1","ActiveInstances"],text=True).strip(); return int(out.split()[-1])
def main():
    p=argparse.ArgumentParser(); p.add_argument("--uuid",default="e20a39f4-73f5-4bc4-a12f-17d1ad07a961"); p.add_argument("--major",type=int,default=10); p.add_argument("--minor",type=int,default=20); p.add_argument("--tx-power",type=int,default=-56); p.add_argument("--adapter",default="hci0"); a=p.parse_args()
    before=active(a.adapter); print("ActiveInstances avant :",before)
    cmd=[sys.executable,"-u","emission_dbus.py","--uuid",a.uuid,"--major",str(a.major),"--minor",str(a.minor),"--tx-power",str(a.tx_power),"--adapter",a.adapter]
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1); registered=False; deadline=time.time()+18
    while time.time()<deadline:
        line=proc.stdout.readline()
        if line: print(line.rstrip()); registered=line.startswith("REGISTERED ") or registered
        if registered or proc.poll() is not None: break
    if not registered:
        print(proc.stderr.read(),file=sys.stderr); proc.terminate(); return 1
    during=active(a.adapter); print("ActiveInstances pendant :",during); print("Vérifie maintenant avec nRF Connect Mobile."); input("Entrée pour arrêter...")
    proc.terminate(); proc.wait(timeout=5); time.sleep(1); after=active(a.adapter); print("ActiveInstances après :",after); return 0 if during>before and after==before else 2
if __name__=="__main__": raise SystemExit(main())
