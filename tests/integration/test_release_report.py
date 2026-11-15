from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hnids.config import HnidsConfig
from hnids.errors import ArtifactIntegrityError
from hnids.security import AuthorizedRoots
from hnids.workflows.release import Gate, evaluate_gates, write_release_report

pytestmark = [pytest.mark.integration, pytest.mark.ml]


def _roots(cfg: HnidsConfig, evidence: Path) -> AuthorizedRoots:
    base = AuthorizedRoots.from_config(cfg.paths)
    return AuthorizedRoots.create(read=base.read_roots, write=(*base.write_roots, evidence))


def _status(gates: list[Gate], prefix: str) -> str:
    return str(next(g.status for g in gates if g.name.startswith(prefix)))


def test_missing_evidence_is_pending_never_pass(
    tiny_bundle: tuple[HnidsConfig, Path], tmp_path: Path
) -> None:
    cfg, bundle = tiny_bundle
    _, gates = evaluate_gates(bundle, tmp_path, _roots(cfg, tmp_path))
    assert _status(gates, "Mean single-window latency") == "PENDING"
    assert _status(gates, "Valid requests") == "PENDING"
    assert _status(gates, "Rollback") == "PENDING"
    assert _status(gates, "SOC replay") == "PENDING"


def test_evidence_drives_pass_and_deviation(
    tiny_bundle: tuple[HnidsConfig, Path], tmp_path: Path
) -> None:
    cfg, bundle = tiny_bundle
    stats = {"mean_ms": 7.0, "p50_ms": 6.0, "p95_ms": 9.0, "p99_ms": 12.0}
    (tmp_path / "latency.json").write_text(
        json.dumps({"end_to_end": stats, "model_only": stats, "host": {"processor": "x"}})
    )
    (tmp_path / "loadtest.json").write_text(
        json.dumps(
            {
                "finite_prediction_rate": 1.0,
                "error_rate": 0.0,
                "requests": 10,
                "windows_per_second": 5.0,
                "flows_per_second": 100.0,
                "latency_ms": {"p95": 20.0},
                "concurrency": 2,
            }
        )
    )
    (tmp_path / "rollback_rehearsal.log").write_text("... ARTIFACT_HASH_MISMATCH ...")
    path = write_release_report(bundle, tmp_path, _roots(cfg, tmp_path))
    _, gates = evaluate_gates(bundle, tmp_path, _roots(cfg, tmp_path))
    assert _status(gates, "Mean single-window latency") == "DEVIATION"  # 7 ms > 5 ms
    assert _status(gates, "Valid requests") == "PASS"
    assert _status(gates, "Rollback") == "PASS"
    text = path.read_text(encoding="utf-8")
    assert "| Mean single-window latency <= 5 ms | DEVIATION |" in text
    assert "## Exceptions" in text


def test_tampered_bundle_cannot_produce_a_report(
    tiny_bundle: tuple[HnidsConfig, Path], tmp_path: Path
) -> None:
    cfg, bundle = tiny_bundle
    copy = cfg.paths.artifact_root / "release-tamper"
    shutil.copytree(bundle, copy)
    (copy / "eval_report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        evaluate_gates(copy, tmp_path, _roots(cfg, tmp_path))
