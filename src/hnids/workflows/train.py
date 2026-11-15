"""End-to-end training workflow: ingest -> prepare -> XGBoost OOF -> hybrid -> calibrate ->
frozen test evaluation -> sealed bundle. Used locally and inside the Kaggle kernel."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import f1_score

from hnids.bundle.package import (
    EVAL_JSON,
    EVAL_MD,
    RUN_MANIFEST,
    BundleMetadata,
    load_bundle,
    new_version,
    write_bundle,
)
from hnids.config.loader import config_hash
from hnids.config.schema import HnidsConfig
from hnids.evaluation.calibration import fit_temperature, softmax
from hnids.evaluation.metrics import EvalReport, evaluate, render_markdown
from hnids.ingestion.pipeline import ingest_all
from hnids.ingestion.schema import FEATURE_SCHEMA_VERSION, MODEL_FEATURE_COLUMNS
from hnids.ingestion.taxonomy import BENIGN, CLASSES, TAXONOMY_VERSION
from hnids.logging import get_logger
from hnids.models.hybrid import ArchitectureSpec, HybridModel
from hnids.models.xgb_oof import fit_oof
from hnids.reproducibility.run_manifest import build_run_manifest, write_run_manifest
from hnids.reproducibility.seeding import seed_everything
from hnids.security.paths import AuthorizedRoots
from hnids.training.loop import WindowSet, predict_logits, select_device, train_hybrid
from hnids.training.prepare import SplitArrays, load_ingested, prepare

_log = get_logger("workflows.train")


def _window_set(split: SplitArrays) -> WindowSet:
    return WindowSet(fused=split.fused(), labels=split.labels, ends=split.windows.ends)


def _evaluate_split(
    split: SplitArrays, logits: np.ndarray, temperature: float, seed: int
) -> EvalReport:
    ends = split.windows.ends
    return evaluate(
        softmax(logits, temperature),
        split.labels[ends],
        split=split.name,
        class_names=CLASSES,
        capture_ids=split.capture_ids[ends],
        timestamps=split.timestamps[ends],
        temperature=temperature,
        seed=seed,
    )


def _xgb_only_macro_f1(split: SplitArrays) -> float:
    """Baseline: the final XGBoost model's per-flow prediction at each window's last flow."""
    if split.probabilities is None or len(split.windows.ends) == 0:
        return 0.0
    ends = split.windows.ends
    truth = split.labels[ends]
    pred = split.probabilities[ends].argmax(axis=1)
    return float(f1_score(truth, pred, labels=np.unique(truth), average="macro", zero_division=0))


def run_training(
    config: HnidsConfig, roots: AuthorizedRoots, *, repo_dir: Path | None = None
) -> dict[str, Any]:
    device = select_device(require_gpu=config.training.require_gpu)  # fail before any work
    seeded = seed_everything(config.run.seed, deterministic=config.run.deterministic)
    manifest = build_run_manifest(config, stage="train", seeded=seeded, repo_dir=repo_dir)
    run_dir = roots.ensure_dir(config.paths.artifact_root / "runs" / manifest.run_id)
    scratch = roots.ensure_dir(
        (config.paths.scratch_root or run_dir) / f"scratch-{manifest.run_id}"
    )
    log = _log.bind(run_id=manifest.run_id)

    # 1. Ingest
    quality = ingest_all(
        config.data,
        roots,
        accepted_dir=scratch / "ingested",
        quarantine_dir=config.paths.quarantine_root / manifest.run_id,
    )
    (run_dir / "quality_report.json").write_text(
        json.dumps(quality.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    log.info("ingested", rows=quality.total_accepted)

    # 2. Split, scale, window
    prepared = prepare(load_ingested(scratch / "ingested", roots), config)
    prepared.split_result.manifest.to_parquet(run_dir / "split_manifest.parquet", index=False)
    (run_dir / "split_support.json").write_text(
        json.dumps(
            {
                "manifest_sha256": prepared.split_result.manifest_sha256,
                "blocks_per_split": prepared.split_result.blocks_per_split,
                "support": prepared.split_result.support.to_dict(),
                "windows": {k: v.windows.stats.__dict__ for k, v in prepared.splits.items()},
                "duplicate_audit": prepared.duplicate_audit,
            },
            indent=2,
            default=int,
        ),
        encoding="utf-8",
    )
    train, val, test = (prepared.splits[s] for s in ("train", "val", "test"))

    # 3. XGBoost OOF probabilities
    oof = fit_oof(
        train.features,
        train.labels,
        train.sub_block_groups(config.xgboost.oof_group_minutes),
        val.features,
        val.labels,
        n_classes=len(CLASSES),
        config=config.xgboost,
        seed=config.run.seed,
    )
    train.probabilities = oof.oof_train
    val.probabilities = oof.final.predict_proba(val.features)
    test.probabilities = oof.final.predict_proba(test.features)

    # 4. Hybrid model
    spec = ArchitectureSpec(
        input_dim=len(MODEL_FEATURE_COLUMNS) + len(CLASSES),
        n_classes=len(CLASSES),
        seq_len=config.sequence.length,
        model=config.model,
    )
    result = train_hybrid(
        _window_set(train),
        _window_set(val),
        spec=spec,
        config=config.training,
        checkpoint_dir=run_dir / "checkpoints",
        roots=roots,
        seed=config.run.seed,
        device=device,
    )

    # 5. Calibrate on validation, evaluate the frozen test split exactly once.
    model = HybridModel(spec).to(device)
    model.load_state_dict(result.state_dict)
    val_logits = predict_logits(model, val.fused(), val.windows.ends, device=device)
    temperature = fit_temperature(val_logits, val.window_labels)
    val_report = _evaluate_split(val, val_logits, temperature, config.run.seed)
    test_logits = predict_logits(model, test.fused(), test.windows.ends, device=device)
    xgb_only = _xgb_only_macro_f1(test)
    test_report = _evaluate_split(test, test_logits, temperature, config.run.seed).model_copy(
        update={"extra": {**prepared.duplicate_audit, "xgb_only_macro_f1": xgb_only}}
    )
    log.info("evaluated", test_macro_f1=test_report.macro_f1, val_macro_f1=val_report.macro_f1)

    # 6. Seal the bundle and prove it reloads through the production path.
    version = new_version()
    metadata = BundleMetadata(
        bundle_version=version,
        created_at=manifest.created_at,
        class_names=CLASSES,
        benign_class=BENIGN,
        taxonomy_version=TAXONOMY_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=MODEL_FEATURE_COLUMNS,
        seq_len=config.sequence.length,
        architecture=spec,
        temperature=temperature,
        alert_threshold=config.serving.alert_threshold,
        low_confidence_threshold=config.serving.low_confidence_threshold,
        xgb_rounds=oof.rounds,
        config_hash=config_hash(config),
        summary_metrics={
            "test_macro_f1": test_report.macro_f1,
            **{f"test_macro_f1_excl_{k}": v for k, v in test_report.macro_f1_excluding.items()},
            "test_benign_fpr": test_report.benign_fpr,
            "test_ece": test_report.ece,
            "val_macro_f1": val_report.macro_f1,
            "test_xgb_only_macro_f1": xgb_only,
        },
    )
    write_run_manifest(manifest, run_dir, roots)
    bundle_dir = write_bundle(
        config.paths.artifact_root / "bundles" / version,
        roots=roots,
        metadata=metadata,
        scaler=prepared.scaler,
        xgb_model=oof.final,
        state_dict=result.state_dict,
        extra_files={
            EVAL_JSON: json.dumps(
                {
                    "test": test_report.model_dump(mode="json"),
                    "val": val_report.model_dump(mode="json"),
                },
                indent=2,
            ),
            EVAL_MD: render_markdown(test_report) + "\n" + render_markdown(val_report),
            RUN_MANIFEST: (run_dir / "run_manifest.json").read_text(encoding="utf-8"),
            "training_history.json": json.dumps(result.history, indent=2),
        },
    )
    reloaded = load_bundle(bundle_dir, roots)
    probe = val.fused()[val.windows.ends[:4, None] + np.arange(-spec.seq_len + 1, 1)]
    with torch.no_grad():
        finite = (
            bool(torch.isfinite(reloaded.model(torch.from_numpy(probe))).all())
            if len(probe)
            else True
        )
    if config.paths.scratch_root is not None:
        shutil.rmtree(scratch, ignore_errors=True)

    summary = {
        "run_id": manifest.run_id,
        "bundle_version": version,
        "bundle_dir": str(bundle_dir),
        "best_epoch": result.best_epoch,
        "xgb_rounds": oof.rounds,
        "temperature": temperature,
        "reload_check_finite": finite,
        "metrics": metadata.summary_metrics,
        "device": str(device),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
