"""Generate the README examples from real held-out test traffic and the published model.

    python scripts/make_examples.py --bundle models/<version> \
        --run-dir artifacts/kaggle-runs/<run>/artifacts/runs/<id>

Writes ``examples/<name>.request.json`` (input) and ``examples/<name>.response.json`` (the
model's actual output) for a few classes, plus a batch request and a streaming sequence.
Flow ids are replaced with neutral ids; feature values are the real CIC-IDS2017 values.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hnids.config import load_config
from hnids.security import AuthorizedRoots
from hnids.serving.app import create_app

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"
CLASSES = ("BENIGN", "DDoS", "PortScan", "DoS", "WebAttack")


def _replay_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "replay", REPO / "scripts" / "replay_test_windows.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load scripts/replay_test_windows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _neutral(payload: dict[str, Any], prefix: str) -> dict[str, Any]:
    for i, flow in enumerate(payload["flows"]):
        flow["flow_id"] = f"{prefix}-flow-{i:02d}"
    return payload


def _write(name: str, data: object) -> None:
    (EXAMPLES / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ingested", type=Path, default=Path("artifacts/dev/ingested"))
    args = parser.parse_args()

    replay = _replay_module()
    windows = replay._windows(args.run_dir, args.ingested, seq_len=20, per_class=3, seed=7)
    by_class: dict[str, Any] = {}
    for window in windows:
        by_class.setdefault(str(window["label"].iloc[-1]), window)

    cfg = load_config(
        [REPO / "configs" / "base.yaml", REPO / "configs" / "local.yaml"], base_dir=REPO
    )
    roots = AuthorizedRoots.from_config(cfg.paths)
    app = create_app(
        bundle_dir=args.bundle.resolve(), roots=roots, serving=cfg.serving, api_key="example"
    )
    EXAMPLES.mkdir(exist_ok=True)
    with TestClient(app, headers={"X-API-Key": "example"}) as client:
        for cls in CLASSES:
            if cls not in by_class:
                continue
            name = cls.lower()
            request = _neutral(replay._payload(by_class[cls]), name)
            response = client.post("/v1/detect", json=request)
            response.raise_for_status()
            _write(f"detect_{name}.request.json", request)
            _write(f"detect_{name}.response.json", response.json())

        batch = {
            "windows": [
                _neutral(replay._payload(by_class[c]), c.lower()) for c in ("BENIGN", "DDoS")
            ]
        }
        _write("detect_batch.request.json", batch)
        _write("detect_batch.response.json", client.post("/v1/detect/batch", json=batch).json())

        stream = _neutral(replay._payload(by_class["PortScan"]), "stream")["flows"]
        for flow in stream:
            flow["session_id"] = "sensor-01"
        replies = [client.post("/v1/flows", json=flow).json() for flow in stream]
        _write("stream_flows.request.json", stream)
        _write("stream_flows.responses.json", replies)

        bad = _neutral(replay._payload(by_class["BENIGN"]), "bad")
        bad["flows"][3]["features"]["flow_duration"] = None
        _write("detect_invalid.request.json", bad)
        _write("detect_invalid.response.json", client.post("/v1/detect", json=bad).json())
    print(f"examples written to {EXAMPLES}")


if __name__ == "__main__":
    main()
