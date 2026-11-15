"""HTTP load test against a running ``hnids serve`` instance.

    python scripts/loadtest.py --url http://127.0.0.1:8000 --seconds 30 --concurrency 16 \
        [--api-key $HNIDS_API_KEY] [--out release/loadtest.json]

Reports sustained windows/s (and flows/s), latency percentiles, error rate and the share of
valid requests that returned a finite, normalized prediction (PRD target >= 99.9%).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

from hnids.ingestion.schema import RAW_FEATURE_COLUMNS


def _window(seq_len: int, session: str, rng: np.random.Generator) -> dict[str, object]:
    start = datetime(2017, 7, 7, 15, 0, tzinfo=UTC)
    return {
        "flows": [
            {
                "session_id": session,
                "timestamp": (start + timedelta(seconds=i)).isoformat(),
                "flow_id": f"{session}-{i}",
                "features": {c: float(abs(rng.normal(100, 50))) for c in RAW_FEATURE_COLUMNS},
            }
            for i in range(seq_len)
        ]
    }


async def _worker(
    client: httpx.AsyncClient,
    payloads: list[dict[str, object]],
    deadline: float,
    stats: dict[str, list[float] | int],
) -> None:
    i = 0
    while time.perf_counter() < deadline:
        body = payloads[i % len(payloads)]
        i += 1
        started = time.perf_counter()
        try:
            response = await client.post("/v1/detect", json=body)
        except httpx.HTTPError:
            stats["errors"] = int(stats["errors"]) + 1  # type: ignore[arg-type]
            continue
        latency = (time.perf_counter() - started) * 1000
        stats["latencies"].append(latency)  # type: ignore[union-attr]
        if response.status_code != 200:
            stats["errors"] = int(stats["errors"]) + 1  # type: ignore[arg-type]
            continue
        probs = response.json()["hnids"]["probabilities"].values()
        if all(math.isfinite(p) for p in probs) and abs(sum(probs) - 1.0) < 1e-3:
            stats["finite"] = int(stats["finite"]) + 1  # type: ignore[arg-type]


async def run(args: argparse.Namespace) -> dict[str, object]:
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    rng = np.random.default_rng(0)
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=10.0) as client:
        ready = (await client.get("/readyz")).json()
        payloads = [_window(args.seq_len, f"s{k}", rng) for k in range(64)]
        stats: dict[str, list[float] | int] = {"latencies": [], "errors": 0, "finite": 0}
        deadline = time.perf_counter() + args.seconds
        started = time.perf_counter()
        await asyncio.gather(
            *[_worker(client, payloads, deadline, stats) for _ in range(args.concurrency)]
        )
        elapsed = time.perf_counter() - started
    latencies = np.array(stats["latencies"])
    total = len(latencies)
    ok = total - int(stats["errors"])  # type: ignore[arg-type]
    return {
        "bundle_version": ready.get("bundle_version"),
        "seconds": elapsed,
        "concurrency": args.concurrency,
        "requests": total,
        "windows_per_second": total / elapsed,
        "flows_per_second": total * args.seq_len / elapsed,
        "error_rate": int(stats["errors"]) / max(total, 1),  # type: ignore[arg-type]
        "finite_prediction_rate": int(stats["finite"]) / max(ok, 1),  # type: ignore[arg-type]
        "latency_ms": {
            "mean": float(latencies.mean()) if total else None,
            "p50": float(np.percentile(latencies, 50)) if total else None,
            "p95": float(np.percentile(latencies, 95)) if total else None,
            "p99": float(np.percentile(latencies, 99)) if total else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--seq-len", type=int, default=20)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
