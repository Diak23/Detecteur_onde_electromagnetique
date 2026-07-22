import queue,subprocess,sys,threading,tkinter as tk
from tkinter import messagebox,ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from analysis_ble import group_events,statistics
from calibration import calculate,load,save
from capture_nrf import NRFCapture
from config import *
from exports import csv_rows,json_file,new_folder,stats_csv
from graphs import save_graphs
from ibeacon import validate
class BLEPlatformV7(tk.Tk):
    def __init__(self):
        super().__init__();self.title(APP_NAME);self.geometry("1280x820");self.frames=[];self.events=[];self.emitter=None;self.capture=None;self.q=queue.Queue();self.cal=load(CALIBRATION_FILE);self._vars();self._ui();self.after(100,self._poll);self.protocol("WM_DELETE_WINDOW",self.close)
    def _vars(self):
        self.uuid=tk.StringVar(value=DEFAULT_UUID);self.major=tk.StringVar(value=str(DEFAULT_MAJOR));self.minor=tk.StringVar(value=str(DEFAULT_MINOR));self.tx=tk.StringVar(value=str(DEFAULT_TX_POWER));self.adapter=tk.StringVar(value=DEFAULT_ADAPTER);self.sniffer=tk.StringVar(value=DEFAULT_SNIFFER);self.window=tk.StringVar(value=str(DEFAULT_GROUP_WINDOW_MS));self.reference=tk.StringVar(value=str(self.cal.get("reference_dbm",-56)));self.emit_state=tk.StringVar(value="Arrêtée");self.capture_state=tk.StringVar(value="Arrêtée");self.count=tk.StringVar(value="0")
    def _row(self,p,n,t,v):ttk.Label(p,text=t).grid(row=n,column=0,sticky="w",padx=8,pady=6);ttk.Entry(p,textvariable=v).grid(row=n,column=1,sticky="ew",padx=8,pady=6)
    def _ui(self):
        nb=ttk.Notebook(self);nb.pack(fill="both",expand=True);self.et=ttk.Frame(nb);self.ct=ttk.Frame(nb);self.at=ttk.Frame(nb);self.gt=ttk.Frame(nb);self.xt=ttk.Frame(nb)
        for f,t in [(self.et,"Émission iBeacon"),(self.ct,"Capture nRF"),(self.at,"Analyse"),(self.gt,"Graphiques"),(self.xt,"Calibration / Export")]:nb.add(f,text=t)
        self._emit_ui();self._capture_ui();self._analysis_ui();self._graph_ui();self._export_ui()
    def _emit_ui(self):
        b=ttk.LabelFrame(self.et,text="Émetteur D-Bus autonome");b.pack(fill="x",padx=15,pady=15);b.columnconfigure(1,weight=1)
        for n,(t,v) in enumerate([("UUID",self.uuid),("Major",self.major),("Minor",self.minor),("Tx Power",self.tx),("Adaptateur",self.adapter)]):self._row(b,n,t,v)
        bar=ttk.Frame(b);bar.grid(row=5,column=0,columnspan=2,pady=10);ttk.Button(bar,text="Démarrer",command=self.start_emit).pack(side="left",padx=5);ttk.Button(bar,text="Arrêter",command=self.stop_emit).pack(side="left",padx=5);ttk.Button(bar,text="Vérifier ActiveInstances",command=self.check_active).pack(side="left",padx=5);ttk.Label(b,textvariable=self.emit_state).grid(row=6,column=0,columnspan=2);self.log=tk.Text(self.et,height=24);self.log.pack(fill="both",expand=True,padx=15,pady=10)
    def _capture_ui(self):
        b=ttk.LabelFrame(self.ct,text="nRF Sniffer");b.pack(fill="x",padx=15,pady=15);b.columnconfigure(1,weight=1);self._row(b,0,"Interface tshark",self.sniffer);self._row(b,1,"Fenêtre regroupement (ms)",self.window);bar=ttk.Frame(b);bar.grid(row=2,column=0,columnspan=2,pady=10);ttk.Button(bar,text="Démarrer capture",command=self.start_capture).pack(side="left",padx=5);ttk.Button(bar,text="Arrêter et analyser",command=self.stop_capture).pack(side="left",padx=5);ttk.Label(b,textvariable=self.capture_state).grid(row=3,column=0);ttk.Label(b,textvariable=self.count).grid(row=3,column=1)
        cols=("time","address","rssi","channel","uuid","major","minor");self.table=ttk.Treeview(self.ct,columns=cols,show="headings")
        for c in cols:self.table.heading(c,text=c);self.table.column(c,width=140,anchor="center")
        self.table.pack(fill="both",expand=True,padx=15,pady=10)
    def _analysis_ui(self):
        ttk.Button(self.at,text="Recalculer",command=self.analyse).pack(pady=8);self.stats=tk.Text(self.at,height=16);self.stats.pack(fill="x",padx=15,pady=8);cols=("index","duration","interval","packets","channels","rssi");self.event_table=ttk.Treeview(self.at,columns=cols,show="headings")
        for c in cols:self.event_table.heading(c,text=c);self.event_table.column(c,width=150,anchor="center")
        self.event_table.pack(fill="both",expand=True,padx=15,pady=8)
    def _graph_ui(self):
        self.fig=Figure(figsize=(9,6),dpi=100);self.ax=self.fig.add_subplot(111);self.canvas=FigureCanvasTkAgg(self.fig,master=self.gt);self.canvas.get_tk_widget().pack(fill="both",expand=True,padx=15,pady=15)
    def _export_ui(self):
        b=ttk.LabelFrame(self.xt,text="Calibration RSSI");b.pack(fill="x",padx=15,pady=15);b.columnconfigure(1,weight=1);self._row(b,0,"RSSI référence",self.reference);ttk.Button(b,text="Calculer calibration",command=self.calibrate).grid(row=1,column=0,columnspan=2,pady=8);ttk.Button(self.xt,text="Exporter CSV / JSON / PNG",command=self.export).pack(pady=15);self.export_log=tk.Text(self.xt,height=24);self.export_log.pack(fill="both",expand=True,padx=15,pady=10)
    def logmsg(self,x):self.q.put(("log",str(x)))
    def _poll(self):
        while True:
            try:k,d=self.q.get_nowait()
            except queue.Empty:break
            if k=="log":
                for w in (self.log,self.export_log):w.insert("end",d+"\n");w.see("end")
            else:
                self.frames.append(d);self.count.set(str(len(self.frames)));self.table.insert("","end",values=(f"{d.timestamp:.6f}",d.address,"" if d.rssi_calibrated is None else f"{d.rssi_calibrated:.1f}",d.channel or "",d.uuid,d.major or "",d.minor or ""))
        self.after(100,self._poll)
    def command(self):
        b=validate(self.uuid.get(),self.major.get(),self.minor.get(),self.tx.get());return [sys.executable,"-u","emission_dbus.py","--uuid",b.uuid,"--major",str(b.major),"--minor",str(b.minor),"--tx-power",str(b.tx_power),"--adapter",self.adapter.get().strip() or "hci0"]
    def start_emit(self):
        if self.emitter and self.emitter.poll() is None:return
        try:self.emitter=subprocess.Popen(self.command(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1);threading.Thread(target=self._read_emitter,daemon=True).start();self.emit_state.set("Démarrage…")
        except Exception as e:messagebox.showerror("Émission",str(e))
    def _read_emitter(self):
        def rd(stream,iserr=False):
            for line in stream:
                self.logmsg(line.rstrip())
                if line.startswith("REGISTERED "):self.emit_state.set("Active")
                if "REGISTER_ERROR" in line or "FATAL" in line:self.emit_state.set("Échec")
        t=threading.Thread(target=rd,args=(self.emitter.stderr,True),daemon=True);t.start();rd(self.emitter.stdout);t.join(timeout=1)
        if self.emit_state.get()!="Échec":self.emit_state.set("Arrêtée")
    def stop_emit(self):
        if self.emitter and self.emitter.poll() is None:
            self.emitter.terminate()
            try:self.emitter.wait(timeout=5)
            except subprocess.TimeoutExpired:self.emitter.kill()
        self.emitter=None;self.emit_state.set("Arrêtée");self.check_active()
    def check_active(self):
        try:o=subprocess.check_output(["busctl","get-property","org.bluez",f"/org/bluez/{self.adapter.get()}","org.bluez.LEAdvertisingManager1","ActiveInstances"],text=True).strip();self.logmsg("ActiveInstances : "+o)
        except Exception as e:self.logmsg("Vérification impossible : "+str(e))
    def start_capture(self):
        if self.capture:return
        try:self.capture=NRFCapture(self.sniffer.get().strip(),lambda f:self.q.put(("frame",f)),self.logmsg,float(self.cal.get("offset_db",0)));self.capture.start();self.capture_state.set("Active")
        except Exception as e:self.capture=None;messagebox.showerror("Capture",str(e))
    def stop_capture(self):
        if self.capture:self.capture.stop();self.capture=None
        self.capture_state.set("Arrêtée");self.analyse()
        if self.frames:self.export(True)
    def analyse(self):
        try:w=float(self.window.get().replace(",","."))
        except:messagebox.showerror("Analyse","Fenêtre invalide");return
        self.events=group_events(self.frames,w);data=statistics(self.frames,self.events);self.stats.delete("1.0","end")
        for k,v in data.items():self.stats.insert("end",f"{k} : {v}\n")
        for x in self.event_table.get_children():self.event_table.delete(x)
        for e in self.events:self.event_table.insert("","end",values=(e.index,f"{e.duration_ms:.3f}","" if e.interval_ms is None else f"{e.interval_ms:.3f}",e.packet_count,e.channels,"" if e.rssi_mean is None else f"{e.rssi_mean:.2f}"))
        self.ax.clear();valid=[x for x in self.frames if x.rssi_calibrated is not None]
        if valid:
            t0=valid[0].timestamp;self.ax.plot([x.timestamp-t0 for x in valid],[x.rssi_calibrated for x in valid]);self.ax.set(title="RSSI calibré",xlabel="Temps (s)",ylabel="RSSI (dBm)");self.ax.grid(True)
        self.fig.tight_layout();self.canvas.draw_idle()
    def calibrate(self):
        try:r=calculate([x.rssi_raw for x in self.frames if x.rssi_raw is not None],float(self.reference.get()));save(CALIBRATION_FILE,r);self.cal=r
        except Exception as e:messagebox.showerror("Calibration",str(e));return
        for x in self.frames:
            if x.rssi_raw is not None:x.rssi_calibrated=x.rssi_raw+r["offset_db"]
        self.analyse();self.logmsg(f"Calibration offset {r['offset_db']:+.2f} dB")
    def export(self,silent=False):
        if not self.frames:
            if not silent:messagebox.showwarning("Export","Aucune mesure")
            return
        self.analyse();folder=new_folder(OUTPUT_DIR);data=statistics(self.frames,self.events);csv_rows(folder/"trames_ble.csv",[x.as_dict() for x in self.frames]);csv_rows(folder/"evenements.csv",[x.as_dict() for x in self.events]);stats_csv(folder/"statistiques.csv",data);json_file(folder/"calibration.json",self.cal);save_graphs(self.frames,self.events,folder/"graphes");self.logmsg("Export : "+str(folder))
        if not silent:messagebox.showinfo("Export",str(folder))
    def close(self):
        try:self.stop_capture()
        except:pass
        try:self.stop_emit()
        except:pass
        self.destroy()
