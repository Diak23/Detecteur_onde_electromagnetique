from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import mean, pstdev
from typing import Optional

@dataclass
class IBeaconEvent:
    index: int
    uuid: str
    major: int
    minor: int
    address: str
    start_epoch: float
    end_epoch: float
    duration_ms: float
    interval_ms: Optional[float]
    packet_count: int
    channels: str
    rssi_mean: Optional[float]
    tx_power: int

    def as_dict(self):
        return asdict(self)

def group_ibeacon_events(frames, window_ms=15.0):
    if not frames:
        return []

    ordered = sorted(frames, key=lambda frame: frame.timestamp)
    groups = []

    for frame in ordered:
        key = (frame.uuid, frame.major, frame.minor)

        if not groups:
            groups.append([frame])
            continue

        previous = groups[-1][-1]
        previous_key = (
            previous.uuid,
            previous.major,
            previous.minor,
        )
        gap_ms = (frame.timestamp - previous.timestamp) * 1000.0

        if key == previous_key and gap_ms <= window_ms:
            groups[-1].append(frame)
        else:
            groups.append([frame])

    last_start_by_beacon = {}
    events = []

    for index, group in enumerate(groups, start=1):
        first = group[0]
        last = group[-1]
        key = (first.uuid, first.major, first.minor)

        interval_ms = None
        if key in last_start_by_beacon:
            interval_ms = (
                first.timestamp - last_start_by_beacon[key]
            ) * 1000.0

        last_start_by_beacon[key] = first.timestamp

        channels = sorted({
            frame.channel
            for frame in group
            if frame.channel is not None
        })

        rssi_values = [
            frame.rssi
            for frame in group
            if frame.rssi is not None
        ]

        events.append(
            IBeaconEvent(
                index=index,
                uuid=first.uuid,
                major=first.major,
                minor=first.minor,
                address=first.address,
                start_epoch=first.timestamp,
                end_epoch=last.timestamp,
                duration_ms=(last.timestamp - first.timestamp) * 1000.0,
                interval_ms=interval_ms,
                packet_count=len(group),
                channels=",".join(str(channel) for channel in channels),
                rssi_mean=mean(rssi_values) if rssi_values else None,
                tx_power=first.tx_power,
            )
        )

    return events

def calculate_statistics(frames, events):
    rssi_values = [
        frame.rssi
        for frame in frames
        if frame.rssi is not None
    ]

    intervals = [
        event.interval_ms
        for event in events
        if event.interval_ms is not None
    ]

    durations = [
        event.duration_ms
        for event in events
    ]

    channel_counts = {
        channel: sum(
            1
            for frame in frames
            if frame.channel == channel
        )
        for channel in (37, 38, 39)
    }

    expected_packets = len(events) * 3
    received_packets = sum(
        min(event.packet_count, 3)
        for event in events
    )

    loss_rate = None
    if expected_packets > 0:
        loss_rate = max(
            0.0,
            100.0 * (expected_packets - received_packets)
            / expected_packets,
        )

    return {
        "nombre_trames_ibeacon": len(frames),
        "nombre_evenements": len(events),
        "nombre_beacons_uniques": len({
            (frame.uuid, frame.major, frame.minor)
            for frame in frames
        }),
        "rssi_moyen_dbm": mean(rssi_values) if rssi_values else None,
        "rssi_min_dbm": min(rssi_values) if rssi_values else None,
        "rssi_max_dbm": max(rssi_values) if rssi_values else None,
        "rssi_ecart_type_db": (
            pstdev(rssi_values)
            if len(rssi_values) > 1
            else (0.0 if rssi_values else None)
        ),
        "intervalle_moyen_ms": (
            mean(intervals)
            if intervals
            else None
        ),
        "duree_evenement_moyenne_ms": (
            mean(durations)
            if durations
            else None
        ),
        "taux_perte_estime_pct": loss_rate,
        "canal_37": channel_counts[37],
        "canal_38": channel_counts[38],
        "canal_39": channel_counts[39],
    }
