"""Train-only feature scaling, persisted as JSON (FR-4, ADR-0004).

Transform: ``signed_log1p`` to tame CICFlowMeter's heavy-tailed counters, followed by
standardization, then clipping to ``±clip``. Parameters are fitted on training rows only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hnids.errors import ArtifactIntegrityError, DataError, ErrorCode

SCALER_VERSION = 1
TRANSFORM = "signed_log1p+standardize+clip"


def signed_log1p(values: np.ndarray) -> np.ndarray:
    result: np.ndarray = np.sign(values) * np.log1p(np.abs(values))
    return result


class FeatureScaler(BaseModel):
    """Fitted scaling parameters. Immutable; JSON is the only persisted form."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = SCALER_VERSION
    transform: str = TRANSFORM
    feature_names: tuple[str, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    clip: float = Field(default=20.0, gt=0.0)
    fitted_rows: int = Field(ge=1)

    @model_validator(mode="after")
    def _lengths_match(self) -> FeatureScaler:
        n = len(self.feature_names)
        if len(self.mean) != n or len(self.scale) != n:
            raise ValueError("mean/scale length must equal number of features")
        if any(s <= 0 or not np.isfinite(s) for s in self.scale):
            raise ValueError("scale entries must be positive and finite")
        if any(not np.isfinite(m) for m in self.mean):
            raise ValueError("mean entries must be finite")
        return self

    @classmethod
    def fit(
        cls, train_features: np.ndarray, feature_names: tuple[str, ...], *, clip: float = 20.0
    ) -> FeatureScaler:
        """Fit on training rows only; callers must never pass validation/test rows."""
        if train_features.ndim != 2 or train_features.shape[1] != len(feature_names):
            raise DataError(
                "feature matrix shape does not match feature names",
                code=ErrorCode.SCHEMA_MISMATCH,
                details={"shape": list(train_features.shape), "features": len(feature_names)},
            )
        if train_features.shape[0] == 0:
            raise DataError("cannot fit scaler on zero rows", code=ErrorCode.DATA_EMPTY)
        if not np.isfinite(train_features).all():
            raise DataError("training features contain non-finite values")
        logged = signed_log1p(train_features.astype(np.float64))
        mean = logged.mean(axis=0)
        std = logged.std(axis=0)
        scale = np.where(std > 1e-12, std, 1.0)
        return cls(
            feature_names=feature_names,
            mean=tuple(float(x) for x in mean),
            scale=tuple(float(x) for x in scale),
            clip=clip,
            fitted_rows=int(train_features.shape[0]),
        )

    def transform_array(self, features: np.ndarray) -> np.ndarray:
        if features.ndim != 2 or features.shape[1] != len(self.feature_names):
            raise DataError(
                "feature matrix does not match scaler schema",
                code=ErrorCode.SCHEMA_MISMATCH,
                details={"shape": list(features.shape), "features": len(self.feature_names)},
            )
        mean = np.asarray(self.mean, dtype=np.float64)
        scale = np.asarray(self.scale, dtype=np.float64)
        scaled = (signed_log1p(features.astype(np.float64)) - mean) / scale
        return np.clip(scaled, -self.clip, self.clip).astype(np.float32)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> FeatureScaler:
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ArtifactIntegrityError(
                f"invalid scaler file {path.name}",
                code=ErrorCode.ARTIFACT_INCOMPATIBLE,
                details={"path": str(path)},
            ) from exc
