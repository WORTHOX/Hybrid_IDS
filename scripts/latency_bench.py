"""Latency benchmark (ADR-0007): model-only and end-to-end single-window inference.

    python scripts/latency_bench.py --bundle <dir> [--iterations 2000] [--out release/latency.json]

Inputs are real held-out windows reconstructed from synthetic-but-schema-valid flows; only the
timing matters here. Runs on the serving reference host (this machine), not on Kaggle.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import torch

from hnids.bundle.package import load_bundle
from hnids.ingestion.schema import RAW_FEATURE_COLUMNS
from hnids.security import AuthorizedRoots
from hnids.serving.engine import InferenceEngine
from hnids.serving.events import FlowRecord


def _flows(seq_len: int, rng: np.random.Generator) -> list[FlowRecord]:
    start = datetime(2017, 7, 7, 15, 0, tzinfo=UTC)
    return [
        FlowRecord(
            session_id="bench",
            timestamp=start + timedelta(seconds=i),
            flow_id=f"f{i}",
            features={c: float(abs(rng.normal(100, 50))) for c in RAW_FEATURE_COLUMNS},
        )
        for i in range(seq_len)
    ]


def _summary(samples_ms: list[float]) -> dict[str, float]:
    arr = np.array(samples_ms)
    return {
        "mean_ms": float(arr.mean()),
        "p50_ms": float(np.percentile(arr, 50)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
        "stdev_ms": float(statistics.pstdev(samples_ms)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    torch.set_num_threads(args.threads)
    bundle_dir = args.bundle.resolve()
    roots = AuthorizedRoots.create(read=[bundle_dir.parent], write=[])
    engine = InferenceEngine(load_bundle(bundle_dir, roots))
    rng = np.random.default_rng(0)
    windows = [_flows(engine.seq_len, rng) for _ in range(32)]

    prepared = [np.stack([p.row for p in engine.prepare_flows(w)])[None] for w in windows]
    for i in range(50):  # warm-up
        engine._probabilities(prepared[i % 32])
        engine.detect_windows([windows[i % 32]])

    model_only, end_to_end = [], []
    for i in range(args.iterations):
        x = prepared[i % 32]
        t0 = time.perf_counter()
        engine._probabilities(x)
        model_only.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        engine.detect_windows([windows[i % 32]])
        end_to_end.append((time.perf_counter() - t0) * 1000)

    result = {
        "bundle_version": engine.meta.bundle_version,
        "measured_at": datetime.now(UTC).isoformat(),
        "host": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "torch_threads": args.threads,
        },
        "iterations": args.iterations,
        "model_only": _summary(model_only),
        "end_to_end": _summary(end_to_end),
        "target_mean_ms": 5.0,
    }
    result["end_to_end_meets_target"] = result["end_to_end"]["mean_ms"] <= 5.0
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
