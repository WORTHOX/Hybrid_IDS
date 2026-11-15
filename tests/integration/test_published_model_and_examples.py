"""The published model and the README examples stay valid and consistent."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hnids.config import load_config
from hnids.security import AuthorizedRoots
from hnids.serving.app import create_app
from hnids.serving.events import DetectionEvent

pytestmark = [pytest.mark.integration, pytest.mark.ml]

REPO = Path(__file__).resolve().parents[2]
MODELS = sorted((REPO / "models").glob("b*"))
EXAMPLES = REPO / "examples"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    if not MODELS:
        pytest.skip("no published model in models/")
    cfg = load_config(
        [REPO / "configs" / "base.yaml", REPO / "configs" / "local.yaml"], base_dir=REPO
    )
    app = create_app(
        bundle_dir=MODELS[-1],
        roots=AuthorizedRoots.from_config(cfg.paths),
        serving=cfg.serving,
        api_key="k",
    )
    with TestClient(app, headers={"X-API-Key": "k"}) as test_client:
        yield test_client


def _load(name: str) -> object:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_published_model_is_ready(client: TestClient) -> None:
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["bundle_version"] == MODELS[-1].name


@pytest.mark.parametrize("name", ["benign", "ddos", "dos", "portscan", "webattack"])
def test_documented_window_examples_reproduce(client: TestClient, name: str) -> None:
    expected = DetectionEvent.model_validate(_load(f"detect_{name}.response.json"))
    actual = DetectionEvent.model_validate(
        client.post("/v1/detect", json=_load(f"detect_{name}.request.json")).json()
    )
    assert actual.hnids.predicted_class == expected.hnids.predicted_class
    assert actual.hnids.confidence == pytest.approx(expected.hnids.confidence, abs=1e-5)
    assert actual.event.kind == expected.event.kind


def test_documented_batch_and_stream_examples(client: TestClient) -> None:
    batch = client.post("/v1/detect/batch", json=_load("detect_batch.request.json")).json()
    expected = _load("detect_batch.response.json")
    assert isinstance(expected, list)
    assert [e["hnids"]["predicted_class"] for e in batch] == [
        e["hnids"]["predicted_class"] for e in expected
    ]
    flows = _load("stream_flows.request.json")
    assert isinstance(flows, list)
    replies = [client.post("/v1/flows", json=f).json() for f in flows]
    assert [r["detection"] is None for r in replies] == [True] * 19 + [False]
    assert replies[-1]["detection"]["hnids"]["predicted_class"] == "PortScan"


def test_documented_invalid_example(client: TestClient) -> None:
    response = client.post("/v1/detect", json=_load("detect_invalid.request.json"))
    assert response.status_code == 422
    assert response.json() == _load("detect_invalid.response.json")
