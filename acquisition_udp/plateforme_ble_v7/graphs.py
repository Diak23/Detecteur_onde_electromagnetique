import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
def save_graphs(frames,events,folder):
    folder.mkdir(parents=True,exist_ok=True)
    valid=[x for x in frames if x.rssi_calibrated is not None]
    if valid:
        t0=valid[0].timestamp;fig,ax=plt.subplots();ax.plot([x.timestamp-t0 for x in valid],[x.rssi_calibrated for x in valid]);ax.set(title="RSSI calibré",xlabel="Temps (s)",ylabel="RSSI (dBm)");ax.grid(True);fig.tight_layout();fig.savefig(folder/"rssi_temps.png",dpi=160);plt.close(fig)
    counts=[sum(x.channel==c for x in frames) for c in (37,38,39)]
    if sum(counts):
        fig,ax=plt.subplots();ax.bar(["37","38","39"],counts);ax.set(title="Répartition des canaux",xlabel="Canal",ylabel="Trames");ax.grid(True,axis="y");fig.tight_layout();fig.savefig(folder/"canaux.png",dpi=160);plt.close(fig)
