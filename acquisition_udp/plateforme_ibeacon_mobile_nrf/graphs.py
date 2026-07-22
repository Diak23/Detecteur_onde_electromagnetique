from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def save_graphs(frames, events, folder):
    folder.mkdir(parents=True, exist_ok=True)
    created = []

    valid_frames = [
        frame for frame in frames
        if frame.rssi is not None
    ]

    if valid_frames:
        t0 = valid_frames[0].timestamp

        figure, axis = plt.subplots()
        axis.plot(
            [frame.timestamp - t0 for frame in valid_frames],
            [frame.rssi for frame in valid_frames],
        )
        axis.set_title("RSSI des iBeacons reçus")
        axis.set_xlabel("Temps (s)")
        axis.set_ylabel("RSSI (dBm)")
        axis.grid(True)
        figure.tight_layout()

        path = folder / "rssi_ibeacon.png"
        figure.savefig(path, dpi=160)
        plt.close(figure)
        created.append(path)

    intervals = [
        event.interval_ms
        for event in events
        if event.interval_ms is not None
    ]

    if intervals:
        figure, axis = plt.subplots()
        axis.plot(
            range(1, len(intervals) + 1),
            intervals,
            marker="o",
        )
        axis.set_title("Intervalle entre advertisements iBeacon")
        axis.set_xlabel("Événement")
        axis.set_ylabel("Intervalle (ms)")
        axis.grid(True)
        figure.tight_layout()

        path = folder / "intervalles_ibeacon.png"
        figure.savefig(path, dpi=160)
        plt.close(figure)
        created.append(path)

    durations = [
        event.duration_ms
        for event in events
    ]

    if durations:
        figure, axis = plt.subplots()
        axis.hist(
            durations,
            bins=min(20, max(5, len(durations))),
        )
        axis.set_title("Durée des événements iBeacon")
        axis.set_xlabel("Durée (ms)")
        axis.set_ylabel("Nombre")
        axis.grid(True)
        figure.tight_layout()

        path = folder / "durees_ibeacon.png"
        figure.savefig(path, dpi=160)
        plt.close(figure)
        created.append(path)

    counts = [
        sum(1 for frame in frames if frame.channel == channel)
        for channel in (37, 38, 39)
    ]

    if sum(counts):
        figure, axis = plt.subplots()
        axis.bar(["37", "38", "39"], counts)
        axis.set_title("Répartition des canaux iBeacon")
        axis.set_xlabel("Canal BLE")
        axis.set_ylabel("Nombre de trames")
        axis.grid(True, axis="y")
        figure.tight_layout()

        path = folder / "canaux_ibeacon.png"
        figure.savefig(path, dpi=160)
        plt.close(figure)
        created.append(path)

    return created
