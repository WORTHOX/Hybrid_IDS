from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hnids.config import HnidsConfig
from hnids.ingestion.schema import RAW_FEATURE_COLUMNS
from hnids.security import AuthorizedRoots
from hnids.serving.app import create_app
from hnids.serving.events import DetectionEvent

pytestmark = [pytest.mark.e2e, pytest.mark.ml]

T0 = datetime(2017, 7, 7, 15, 0)
KEY = "test-key"


def flow(i: int, session: str = "sensor-1", **overrides: Any) -> dict[str, Any]:
    features: dict[str, Any] = dict.fromkeys(RAW_FEATURE_COLUMNS, 10.0 + i % 3)
    features["protocol"] = 6.0
    features.update(overrides)
    return {
        "session_id": session,
        "timestamp": (T0 + timedelta(seconds=i)).isoformat(),
        "flow_id": f"f{i}",
        "features": features,
    }


def window(n: int, session: str = "sensor-1", start: int = 0) -> dict[str, Any]:
    return {"flows": [flow(start + i, session) for i in range(n)]}


@pytest.fixture
def client(tiny_bundle: tuple[HnidsConfig, Path]) -> Iterator[TestClient]:
    cfg, bundle_dir = tiny_bundle
    app = create_app(
        bundle_dir=bundle_dir,
        roots=AuthorizedRoots.from_config(cfg.paths),
        serving=cfg.serving.model_copy(update={"max_batch": 3}),
        api_key=KEY,
    )
    with TestClient(app, headers={"X-API-Key": KEY}) as test_client:
        yield test_client


def seq_len(cfg: HnidsConfig) -> int:
    return cfg.sequence.length


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "alive"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True


def test_detect_returns_schema_valid_event(
    client: TestClient, tiny_bundle: tuple[HnidsConfig, Path]
) -> None:
    cfg, _ = tiny_bundle
    response = client.post("/v1/detect", json=window(seq_len(cfg)))
    assert response.status_code == 200, response.text
    event = DetectionEvent.model_validate(response.json())
    assert sum(event.hnids.probabilities.values()) == pytest.approx(1.0, abs=1e-4)
    assert event.hnids.predicted_class in event.hnids.probabilities
    assert event.hnids.validation_status == "ok"
    assert event.event.kind in {"alert", "signal"}
    assert "sensor-1" not in json.dumps(response.json())  # identifiers are hashed


def test_rate_infinity_is_sanitized_not_rejected(
    client: TestClient, tiny_bundle: tuple[HnidsConfig, Path]
) -> None:
    cfg, _ = tiny_bundle
    body = window(seq_len(cfg))
    body["flows"][-1]["features"]["flow_bytes_s"] = None
    event = client.post("/v1/detect", json=body).json()
    assert event["hnids"]["validation_status"] == "sanitized"
    assert event["hnids"]["sanitized_values"] == 1


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda b: b["flows"].pop(), "INPUT_INVALID"),  # wrong window length
        (lambda b: b["flows"][0]["features"].pop("flow_duration"), "SCHEMA_MISMATCH"),
        (lambda b: b["flows"][0]["features"].update(evil=1.0), "SCHEMA_MISMATCH"),
        (lambda b: b["flows"][0]["features"].update(flow_duration=None), "INPUT_INVALID"),
        (lambda b: b["flows"][1].update(session_id="other"), "INPUT_INVALID"),
        (lambda b: b["flows"].reverse(), "INPUT_INVALID"),  # not chronological
        (lambda b: b["flows"][0].update(timestamp="not-a-time"), "INPUT_INVALID"),
        (lambda b: b["flows"][0].update(extra_field=1), "INPUT_INVALID"),
    ],
)
def test_invalid_windows_never_produce_detections(
    client: TestClient, tiny_bundle: tuple[HnidsConfig, Path], mutate: Any, code: str
) -> None:
    cfg, _ = tiny_bundle
    body = window(seq_len(cfg))
    mutate(body)
    response = client.post("/v1/detect", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert "detection_id" not in response.text


def test_batch_and_batch_limit(client: TestClient, tiny_bundle: tuple[HnidsConfig, Path]) -> None:
    cfg, _ = tiny_bundle
    n = seq_len(cfg)
    ok = client.post("/v1/detect/batch", json={"windows": [window(n), window(n, "s2")]})
    assert ok.status_code == 200
    assert len(ok.json()) == 2
    too_big = client.post("/v1/detect/batch", json={"windows": [window(n)] * 4})
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "BATCH_TOO_LARGE"


def test_batch_is_atomic(client: TestClient, tiny_bundle: tuple[HnidsConfig, Path]) -> None:
    cfg, _ = tiny_bundle
    bad = window(seq_len(cfg))
    bad["flows"][0]["features"]["flow_iat_max"] = None
    response = client.post("/v1/detect/batch", json={"windows": [window(seq_len(cfg)), bad]})
    assert response.status_code == 422


def test_streaming_buffers_per_session(
    client: TestClient, tiny_bundle: tuple[HnidsConfig, Path]
) -> None:
    cfg, _ = tiny_bundle
    n = seq_len(cfg)
    for i in range(n - 1):
        r = client.post("/v1/flows", json=flow(i, "a")).json()
        assert r["detection"] is None
        assert r["buffered"] == i + 1
        # Interleaved session must not contaminate session "a".
        assert client.post("/v1/flows", json=flow(i, "b")).json()["buffered"] == i + 1
    done = client.post("/v1/flows", json=flow(n - 1, "a")).json()
    assert done["detection"] is not None
    DetectionEvent.model_validate(done["detection"])


def test_invalid_flow_does_not_touch_buffer(client: TestClient) -> None:
    client.post("/v1/flows", json=flow(0, "c"))
    bad = flow(1, "c", flow_iat_mean=None)
    assert client.post("/v1/flows", json=bad).status_code == 422
    assert client.post("/v1/flows", json=flow(2, "c")).json()["buffered"] == 2


def test_out_of_order_flow_resets_session(client: TestClient) -> None:
    client.post("/v1/flows", json=flow(10, "d"))
    client.post("/v1/flows", json=flow(11, "d"))
    assert client.post("/v1/flows", json=flow(5, "d")).json()["buffered"] == 1


def test_api_key_required(tiny_bundle: tuple[HnidsConfig, Path]) -> None:
    cfg, bundle_dir = tiny_bundle
    app = create_app(
        bundle_dir=bundle_dir,
        roots=AuthorizedRoots.from_config(cfg.paths),
        serving=cfg.serving,
        api_key=KEY,
    )
    with TestClient(app) as anonymous:
        response = anonymous.post("/v1/detect", json=window(seq_len(cfg)))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
        assert anonymous.get("/healthz").status_code == 200


def test_metrics_exposed(client: TestClient, tiny_bundle: tuple[HnidsConfig, Path]) -> None:
    cfg, _ = tiny_bundle
    client.post("/v1/detect", json=window(seq_len(cfg)))
    client.post("/v1/detect", json={"flows": []})
    text = client.get("/metrics").text
    for name in (
        "hnids_requests_total",
        "hnids_request_seconds",
        "hnids_rejections_total",
        "hnids_detections_total",
        "hnids_ready 1.0",
    ):
        assert name in text


def test_tampered_bundle_never_becomes_ready(tiny_bundle: tuple[HnidsConfig, Path]) -> None:
    cfg, bundle_dir = tiny_bundle
    tampered = cfg.paths.artifact_root / "tampered-serving"
    shutil.copytree(bundle_dir, tampered)
    (tampered / "scaler.json").write_text("{}", encoding="utf-8")
    app = create_app(
        bundle_dir=tampered,
        roots=AuthorizedRoots.from_config(cfg.paths),
        serving=cfg.serving,
        api_key=KEY,
    )
    with TestClient(app, headers={"X-API-Key": KEY}) as c:
        ready = c.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "ARTIFACT_HASH_MISMATCH"
        detect = c.post("/v1/detect", json=window(seq_len(cfg)))
        assert detect.status_code == 503
        assert detect.json()["error"]["code"] == "MODEL_NOT_READY"
        assert "hnids_ready 0.0" in c.get("/metrics").text


def test_event_json_schema_matches_published_contract() -> None:
    published = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "detection-event.v1.json"
    assert json.loads(published.read_text(encoding="utf-8")) == DetectionEvent.model_json_schema()
