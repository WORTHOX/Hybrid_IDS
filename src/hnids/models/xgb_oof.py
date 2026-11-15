"""XGBoost out-of-fold probability features (FR-5).

* The final model is trained on the full training partition, with early stopping on
  **validation** to choose the number of boosting rounds ``K``.
* Training rows get out-of-fold probabilities from ``StratifiedGroupKFold`` models (groups are
  time blocks) trained with exactly ``K`` rounds. No row is scored by a model that saw it, and
  the held-out fold never influences its own model.
* The final model scores validation, test and production rows.

Models are persisted with XGBoost's native UBJSON format, never pickle (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.model_selection import StratifiedGroupKFold

from hnids.config.schema import XGBoostConfig
from hnids.errors import ArtifactIntegrityError, DataError, ErrorCode
from hnids.logging import get_logger

PROBABILITY_TOLERANCE = 1e-4

_log = get_logger("models.xgb")


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch
    except ImportError:  # pragma: no cover - torch is part of the ml extra
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def balanced_sample_weights(labels: np.ndarray, n_classes: int) -> np.ndarray:
    """Inverse-square-root class frequency weights, normalized to mean 1."""
    counts = np.bincount(labels, minlength=n_classes).astype(np.float64)
    per_class = np.where(counts > 0, 1.0 / np.sqrt(np.maximum(counts, 1.0)), 0.0)
    weights = per_class[labels]
    normalized: np.ndarray = (weights / weights.mean()).astype(np.float32)
    return normalized


def _params(config: XGBoostConfig, n_classes: int, seed: int) -> dict[str, object]:
    return {
        "objective": "multi:softprob",
        "num_class": n_classes,
        "max_depth": config.max_depth,
        "eta": config.learning_rate,
        "subsample": config.subsample,
        "colsample_bytree": config.colsample_bytree,
        "tree_method": "hist",
        "device": resolve_device(config.device),
        "eval_metric": "mlogloss",
        "seed": seed,
    }


def check_probabilities(probs: np.ndarray, n_classes: int) -> np.ndarray:
    """Assert one finite probability per class, rows summing to one."""
    if probs.ndim != 2 or probs.shape[1] != n_classes:
        raise DataError(
            f"probability matrix has shape {probs.shape}, expected (*, {n_classes})",
            code=ErrorCode.SCHEMA_MISMATCH,
        )
    if not np.isfinite(probs).all():
        raise DataError("non-finite probabilities produced", code=ErrorCode.INPUT_INVALID)
    if probs.shape[0] and not np.allclose(probs.sum(axis=1), 1.0, atol=PROBABILITY_TOLERANCE):
        raise DataError("probabilities do not sum to one", code=ErrorCode.INPUT_INVALID)
    return probs.astype(np.float32)


@dataclass
class XGBProbabilityModel:
    booster: xgb.Booster
    n_classes: int

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        raw = self.booster.inplace_predict(np.ascontiguousarray(features, dtype=np.float32))
        probs = np.asarray(raw, dtype=np.float64).reshape(-1, self.n_classes)
        return check_probabilities(probs, self.n_classes)

    def save(self, path: Path) -> None:
        self.booster.save_model(str(path))

    @classmethod
    def load(cls, path: Path, n_classes: int) -> XGBProbabilityModel:
        booster = xgb.Booster()
        try:
            booster.load_model(str(path))
        except xgb.core.XGBoostError as exc:
            raise ArtifactIntegrityError(
                f"cannot load XGBoost model {path.name}",
                code=ErrorCode.ARTIFACT_INCOMPATIBLE,
            ) from exc
        booster.set_param({"device": "cpu"})
        return cls(booster=booster, n_classes=n_classes)


@dataclass(frozen=True)
class OOFResult:
    oof_train: np.ndarray  # (n_train, C) probabilities from models that never saw each row
    final: XGBProbabilityModel
    rounds: int
    fold_sizes: tuple[int, ...]


def _train_booster(
    params: dict[str, object],
    features: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    rounds: int,
    evals: tuple[np.ndarray, np.ndarray] | None = None,
    early_stopping: int | None = None,
) -> xgb.Booster:
    dtrain = xgb.QuantileDMatrix(features, label=labels, weight=weights)
    eval_list = []
    if evals is not None:
        eval_list = [(xgb.DMatrix(evals[0], label=evals[1]), "val")]
    return xgb.train(
        params,
        dtrain,
        num_boost_round=rounds,
        evals=eval_list,
        early_stopping_rounds=early_stopping if evals is not None else None,
        verbose_eval=False,
    )


def fit_oof(
    train_x: np.ndarray,
    train_y: np.ndarray,
    train_groups: np.ndarray,
    val_x: np.ndarray,
    val_y: np.ndarray,
    *,
    n_classes: int,
    config: XGBoostConfig,
    seed: int,
) -> OOFResult:
    """Fit the final model and produce leakage-free OOF probabilities for training rows."""
    if len(train_x) != len(train_y) or len(train_x) != len(train_groups):
        raise DataError(
            "train features, labels and groups must align", code=ErrorCode.SCHEMA_MISMATCH
        )
    params = _params(config, n_classes, seed)
    weights = balanced_sample_weights(train_y, n_classes)

    final_booster = _train_booster(
        params,
        train_x,
        train_y,
        weights,
        config.n_estimators,
        evals=(val_x, val_y) if len(val_x) else None,
        early_stopping=config.early_stopping_rounds,
    )
    best = getattr(final_booster, "best_iteration", None)
    rounds = int(best) + 1 if best is not None and len(val_x) else config.n_estimators
    if rounds < final_booster.num_boosted_rounds():
        final_booster = final_booster[:rounds]
    final = XGBProbabilityModel(booster=final_booster, n_classes=n_classes)
    _log.info("xgb_final_trained", rounds=rounds, device=params["device"], rows=len(train_x))

    n_groups = len(np.unique(train_groups))
    folds = min(config.n_folds, n_groups)
    if folds < 2:
        raise DataError(
            "need at least two training groups for OOF folds", code=ErrorCode.SPLIT_INVALID
        )
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    oof = np.full((len(train_x), n_classes), np.nan, dtype=np.float64)
    sizes: list[int] = []
    for fold, (fit_idx, hold_idx) in enumerate(splitter.split(train_x, train_y, train_groups)):
        booster = _train_booster(
            params, train_x[fit_idx], train_y[fit_idx], weights[fit_idx], rounds
        )
        model = XGBProbabilityModel(booster=booster, n_classes=n_classes)
        oof[hold_idx] = model.predict_proba(train_x[hold_idx])
        sizes.append(len(hold_idx))
        _log.info("xgb_fold_done", fold=fold, held_out=len(hold_idx))
    return OOFResult(
        oof_train=check_probabilities(oof, n_classes),
        final=final,
        rounds=rounds,
        fold_sizes=tuple(sizes),
    )
