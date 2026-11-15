"""Synthetic TrafficLabelling-style CSVs reproducing the real files' quirks.

Quirks: cp1252 encoding with an en-dash in web-attack labels, leading spaces in headers, a
duplicated ``Fwd Header Length`` column, fully empty padding rows, ``Infinity``/``NaN`` in rate
features, a bare 12-hour afternoon clock, and rows out of chronological order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from hnids.ingestion.schema import IDENTIFIER_COLUMNS, RAW_FEATURE_COLUMNS

WEB_XSS = "Web Attack " + chr(0x2013) + " XSS"  # encoded to 0x96 by cp1252

# Class-dependent feature offsets so tiny models can learn a separable signal.
_LABEL_SIGNAL = {
    "BENIGN": 0.0,
    "DoS Hulk": 5.0,
    "DDoS": 9.0,
    "PortScan": -4.0,
    WEB_XSS: 13.0,
}


def header() -> list[str]:
    cols = [f" {c.replace('_', ' ').title()}" for c in IDENTIFIER_COLUMNS]
    feats = [f" {c}" for c in RAW_FEATURE_COLUMNS]
    # The real file repeats "Fwd Header Length" near the end.
    insert_at = feats.index(" avg_bwd_segment_size") + 1
    feats.insert(insert_at, " fwd_header_length")
    return ["Flow ID", *cols[1:], *feats, " Label"]


@dataclass(frozen=True)
class Flow:
    when: datetime
    label: str
    rate: str | None = None  # override for flow_bytes_s, e.g. "Infinity" / "NaN"
    timestamp_text: str | None = None  # override the rendered timestamp
    feature_override: tuple[str, str] | None = None  # (column, raw text)


def _render_time(when: datetime, twelve_hour: bool) -> str:
    hour = when.hour % 12 if twelve_hour and when.hour > 12 else when.hour
    return f"{when.day}/{when.month}/{when.year} {hour}:{when.minute:02d}"


def write_capture(
    path: Path,
    flows: Sequence[Flow],
    *,
    twelve_hour_afternoon: bool = True,
    empty_rows: int = 3,
    seed: int = 0,
) -> Path:
    rng = np.random.default_rng(seed)
    names = header()
    lines = [",".join(names)]
    for i, flow in enumerate(flows):
        signal = _LABEL_SIGNAL.get(flow.label, 0.0)
        values = {c: f"{abs(rng.normal(10 + signal, 1)):.3f}" for c in RAW_FEATURE_COLUMNS}
        values["protocol"] = "6"
        if flow.rate is not None:
            values["flow_bytes_s"] = flow.rate
        if flow.feature_override is not None:
            values[flow.feature_override[0]] = flow.feature_override[1]
        ts = flow.timestamp_text or _render_time(flow.when, twelve_hour_afternoon)
        ident = [
            f"10.0.0.{i % 250}-10.0.1.1-{1000 + i}-80-6",
            f"10.0.0.{i % 250}",
            str(1000 + i),
            "10.0.1.1",
            "80",
            ts,
        ]
        feats = [values[c] for c in RAW_FEATURE_COLUMNS]
        insert_at = RAW_FEATURE_COLUMNS.index("avg_bwd_segment_size") + 1
        feats.insert(insert_at, values["fwd_header_length"])
        lines.append(",".join([*ident, *feats, flow.label]))
    lines.extend("," * (len(names) - 1) for _ in range(empty_rows))
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))
    return path


def timeline(
    start: datetime,
    minutes: int,
    per_minute: int,
    attacks: dict[str, tuple[int, int]] | None = None,
) -> list[Flow]:
    """Flows spread over ``minutes``; ``attacks`` maps label -> (start_minute, end_minute)."""
    flows: list[Flow] = []
    for minute in range(minutes):
        label = "BENIGN"
        for name, (lo, hi) in (attacks or {}).items():
            if lo <= minute < hi:
                label = name
        for k in range(per_minute):
            current = label if k % 2 == 0 else "BENIGN"
            flows.append(Flow(when=start + timedelta(minutes=minute), label=current))
    return flows
