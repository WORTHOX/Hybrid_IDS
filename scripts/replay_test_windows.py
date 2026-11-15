"""Replay held-out *test* windows from a training run through the live HTTP service.

    python scripts/replay_test_windows.py --run-dir <kaggle-run>/artifacts/runs/<id> \
        --ingested artifacts/dev/ingested --per-class 200 [--api-key KEY] \
        [--out release/replay.json]

Rebuilds windows exactly as training did: test split, sorted by (capture, time, source row), and
never crossing a 5-minute block. It sends each window to ``POST /v1/detect`` and compares the
predicted class with the true label of the window's last flow. Ingested rate features that
were zero-filled are sent as ``null`` so the service sanitizes them the same way.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, f1_score

from hnids.ingestion.schema import RATE_FEATURES, RATE_NONFINITE, RAW_FEATURE_COLUMNS


def _windows(
    run_dir: Path, ingested: Path, seq_len: int, per_class: int, seed: int
) -> list[pd.DataFrame]:
    manifest = pd.read_parquet(run_dir / "split_manifest.parquet")
    test = manifest[manifest["split"] == "test"]
    frames = []
    for capture, keys in test.groupby("capture_id"):
        data = pd.read_parquet(ingested / f"{capture}.parquet")
        frames.append(data.merge(keys, on=["capture_id", "source_row_index"]))
    rows = pd.concat(frames).sort_values(
        ["capture_id", "timestamp", "source_row_index"], kind="stable"
    )
    rows = rows.reset_index(drop=True)
    block = rows["block_id"].to_numpy()
    starts = np.arange(len(rows)) - seq_len + 1
    valid = starts >= 0
    valid[valid] = block[starts[valid]] == block[np.arange(len(rows))[valid]]
    ends = np.flatnonzero(valid)
    labels = rows["label"].to_numpy()[ends]
    rng = np.random.default_rng(seed)
    chosen: list[int] = []
    for cls in np.unique(labels):
        candidates = ends[labels == cls]
        chosen.extend(rng.choice(candidates, size=min(per_class, len(candidates)), replace=False))
    return [rows.iloc[e - seq_len + 1 : e + 1] for e in sorted(chosen)]


def _payload(window: pd.DataFrame) -> dict[str, object]:
    flows = []
    for _, row in window.iterrows():
        features: dict[str, float | None] = {c: float(row[c]) for c in RAW_FEATURE_COLUMNS}
        if row[RATE_NONFINITE] == 1.0:
            for name in RATE_FEATURES:
                if features[name] == 0.0:
                    features[name] = None
        flows.append(
            {
                "session_id": str(row["capture_id"]),
                "timestamp": pd.Timestamp(row["timestamp"]).tz_localize(UTC).isoformat(),
                "flow_id": str(row["flow_id"]),
                "features": features,
            }
        )
    return {"flows": flows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ingested", type=Path, default=Path("artifacts/dev/ingested"))
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--seq-len", type=int, default=20)
    parser.add_argument("--per-class", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    windows = _windows(args.run_dir, args.ingested, args.seq_len, args.per_class, args.seed)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    truth, pred, failures = [], [], 0
    with httpx.Client(base_url=args.url, headers=headers, timeout=30.0) as client:
        version = client.get("/readyz").json().get("bundle_version")
        for window in windows:
            response = client.post("/v1/detect", json=_payload(window))
            if response.status_code != 200:
                failures += 1
                continue
            truth.append(str(window["label"].iloc[-1]))
            pred.append(response.json()["hnids"]["predicted_class"])
    labels = sorted(set(truth))
    result = {
        "bundle_version": version,
        "windows_sent": len(windows),
        "http_failures": failures,
        "macro_f1": float(f1_score(truth, pred, labels=labels, average="macro", zero_division=0)),
        "accuracy": float(np.mean(np.array(truth) == np.array(pred))),
        "per_class": classification_report(
            truth, pred, labels=labels, output_dict=True, zero_division=0
        ),
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
