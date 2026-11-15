from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hnids.bundle.package import (
    WEIGHTS,
    activate,
    active_bundle_dir,
    import_bundle,
    load_bundle,
    read_active,
    rollback,
)
from hnids.config import HnidsConfig, load_config
from hnids.errors import ArtifactIntegrityError, ErrorCode
from hnids.security import AuthorizedRoots
from hnids.workflows.train import run_training
from tests.conftest import CONFIG_DIR
from tests.synthetic import WEB_XSS, timeline, write_capture

pytestmark = [pytest.mark.integration, pytest.mark.ml]

TINY: dict[str, Any] = {
    "run": {"name": "it", "seed": 1, "log_json": True, "log_level": "WARNING"},
    "split": {"block_minutes": 5},
    "sequence": {"length": 5},
    "xgboost": {
        "n_folds": 3,
        "n_estimators": 12,
        "max_depth": 3,
        "early_stopping_rounds": 4,
        "device": "cpu",
    },
    "model": {
        "gru_hidden": 8,
        "gru_layers": 1,
        "d_model": 16,
        "n_heads": 2,
        "ff_dim": 32,
        "transformer_layers": 1,
    },
    "training": {"epochs": 2, "batch_size": 64, "early_stopping_patience": 2, "amp": False},
}


def tiny_config(root: Path) -> HnidsConfig:
    for d in ("data", "artifacts", "quarantine", "scratch"):
        (root / d).mkdir(exist_ok=True)
    return load_config(
        [CONFIG_DIR / "base.yaml"],
        overrides={
            **TINY,
            "paths": {
                "data_roots": [str(root / "data")],
                "artifact_root": str(root / "artifacts"),
                "quarantine_root": str(root / "quarantine"),
                "scratch_root": str(root / "scratch"),
            },
        },
    )


def write_dataset(data_dir: Path) -> None:
    morning = datetime(2017, 7, 5, 9, 0)
    afternoon = datetime(2017, 7, 7, 13, 0)
    write_capture(
        data_dir / "Wed.pcap_ISCX.csv",
        timeline(morning, 150, 6, {"DoS Hulk": (20, 120)}),
        seed=1,
    )
    write_capture(
        data_dir / "Fri-PM.pcap_ISCX.csv",
        timeline(afternoon, 150, 6, {"DDoS": (10, 70), "PortScan": (80, 140)}),
        seed=2,
    )
    write_capture(
        data_dir / "Thu.pcap_ISCX.csv",
        timeline(datetime(2017, 7, 6, 9, 0), 150, 6, {WEB_XSS: (30, 110)}),
        seed=3,
    )


@pytest.fixture(scope="module")
def trained(tmp_path_factory: pytest.TempPathFactory) -> tuple[HnidsConfig, dict[str, Any]]:
    root = tmp_path_factory.mktemp("pipeline")
    config = tiny_config(root)
    write_dataset(root / "data")
    summary = run_training(config, AuthorizedRoots.from_config(config.paths), repo_dir=root)
    return config, summary


def test_training_produces_verified_bundle(trained: tuple[HnidsConfig, dict[str, Any]]) -> None:
    config, summary = trained
    roots = AuthorizedRoots.from_config(config.paths)
    bundle = load_bundle(Path(summary["bundle_dir"]), roots)
    assert bundle.metadata.class_names[0] == "BENIGN"
    assert bundle.metadata.architecture.input_dim == 78 + 8
    assert summary["reload_check_finite"] is True
    assert 0.05 <= summary["temperature"] <= 20.0
    # The synthetic classes are separable: the pipeline must actually learn.
    assert summary["metrics"]["test_macro_f1"] > 0.5


def test_run_artifacts_and_leakage_evidence(trained: tuple[HnidsConfig, dict[str, Any]]) -> None:
    config, summary = trained
    run_dir = config.paths.artifact_root / "runs" / summary["run_id"]
    support = json.loads((run_dir / "split_support.json").read_text(encoding="utf-8"))
    assert len(support["manifest_sha256"]) == 64
    assert set(support["windows"]) == {"train", "val", "test"}
    assert "test_rows_identical_to_a_train_row" in support["duplicate_audit"]
    assert (run_dir / "checkpoints" / "best.safetensors").is_file()
    assert (run_dir / "run_manifest.json").is_file()
    # Scratch intermediates are cleaned up when a scratch root is configured.
    assert not any(config.paths.scratch_root.iterdir())  # type: ignore[union-attr]

    report = json.loads(
        (Path(summary["bundle_dir"]) / "eval_report.json").read_text(encoding="utf-8")
    )
    test = report["test"]
    assert "Rare" in test["macro_f1_excluding"]
    assert len(test["confusion"]) == 8
    assert 0.0 <= test["ece"] <= 1.0


def test_tampered_bundle_is_refused(
    trained: tuple[HnidsConfig, dict[str, Any]], tmp_path: Path
) -> None:
    config, summary = trained
    roots = AuthorizedRoots.from_config(config.paths)
    copy = config.paths.artifact_root / "tamper-copy"
    import shutil

    shutil.copytree(summary["bundle_dir"], copy)
    weights = copy / WEIGHTS
    data = bytearray(weights.read_bytes())
    data[-1] ^= 0xFF
    weights.write_bytes(bytes(data))
    with pytest.raises(ArtifactIntegrityError) as info:
        load_bundle(copy, roots)
    assert info.value.code is ErrorCode.ARTIFACT_HASH_MISMATCH


def test_activate_and_rollback(trained: tuple[HnidsConfig, dict[str, Any]]) -> None:
    config, summary = trained
    roots = AuthorizedRoots.from_config(config.paths)
    registry = config.paths.artifact_root / "registry"
    roots.ensure_dir(registry)
    first = import_bundle(Path(summary["bundle_dir"]), registry, roots)
    second = registry / "b-second"
    import shutil

    shutil.copytree(first, second)
    meta_path = second / "bundle.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["bundle_version"] = "b-second"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    from hnids.security import write_manifest

    write_manifest(second)

    with pytest.raises(ArtifactIntegrityError):
        rollback(registry, roots, operator="t")  # nothing active yet
    activate(registry, first.name, roots, operator="t")
    pointer = activate(registry, "b-second", roots, operator="t")
    assert (pointer.active, pointer.previous) == ("b-second", first.name)
    assert active_bundle_dir(registry, roots).name == "b-second"
    back = rollback(registry, roots, operator="t")
    assert (back.active, back.previous) == (first.name, "b-second")
    assert read_active(registry, roots) == back
    with pytest.raises(ArtifactIntegrityError):
        import_bundle(Path(summary["bundle_dir"]), registry, roots)  # duplicate import


def test_activate_refuses_tampered_bundle(trained: tuple[HnidsConfig, dict[str, Any]]) -> None:
    config, summary = trained
    roots = AuthorizedRoots.from_config(config.paths)
    registry = config.paths.artifact_root / "registry2"
    roots.ensure_dir(registry)
    target = import_bundle(Path(summary["bundle_dir"]), registry, roots)
    (target / "bundle.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        activate(registry, target.name, roots, operator="t")
    assert read_active(registry, roots) is None


def test_bundle_prediction_matches_training_path(
    trained: tuple[HnidsConfig, dict[str, Any]],
) -> None:
    config, summary = trained
    roots = AuthorizedRoots.from_config(config.paths)
    bundle = load_bundle(Path(summary["bundle_dir"]), roots)
    x = np.zeros((3, len(bundle.metadata.feature_names)), dtype=np.float32)
    probs = bundle.xgb.predict_proba(bundle.scaler.transform_array(x))
    assert probs.shape == (3, 8)
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-4)
