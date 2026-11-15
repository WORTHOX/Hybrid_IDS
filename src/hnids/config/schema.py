"""Typed, immutable run configuration (FR-1).

All models are frozen and reject unknown keys, so a typo in a YAML file fails fast instead of
silently falling back to a default.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_SCHEMA_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class RunConfig(_Frozen):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    deterministic: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True


class PathsConfig(_Frozen):
    """Authorized storage roots. Every file access must resolve inside one of these."""

    data_roots: tuple[Path, ...] = Field(min_length=1)
    artifact_root: Path
    quarantine_root: Path
    scratch_root: Path | None = None  # large intermediates; not persisted on Kaggle

    @field_validator("data_roots", "artifact_root", "quarantine_root", "scratch_root")
    @classmethod
    def _must_be_absolute(
        cls, value: Path | tuple[Path, ...] | None
    ) -> Path | tuple[Path, ...] | None:
        if value is None:
            return value
        paths = value if isinstance(value, tuple) else (value,)
        for path in paths:
            if not path.is_absolute():
                raise ValueError(f"path must be absolute after loading: {path}")
        return value


class DataConfig(_Frozen):
    source_glob: str = "**/*.pcap_ISCX.csv"
    encoding: str = "cp1252"
    chunk_rows: int = Field(default=200_000, ge=1_000)
    max_files: int | None = Field(default=None, ge=1)
    max_rows_per_file: int | None = Field(default=None, ge=1)


class SplitConfig(_Frozen):
    block_minutes: int = Field(default=5, ge=1, le=240)
    train_ratio: float = Field(default=0.70, gt=0.0, lt=1.0)
    val_ratio: float = Field(default=0.15, gt=0.0, lt=1.0)
    test_ratio: float = Field(default=0.15, gt=0.0, lt=1.0)
    purge_minutes: int = Field(default=0, ge=0, le=60)
    stratify_by: Literal["label", "label_raw"] = "label_raw"

    @model_validator(mode="after")
    def _ratios_sum_to_one(self) -> SplitConfig:
        total = self.train_ratio + self.val_ratio + self.test_ratio
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"split ratios must sum to 1.0, got {total}")
        return self


class SequenceConfig(_Frozen):
    length: int = Field(default=20, ge=2, le=512)


class XGBoostConfig(_Frozen):
    n_folds: int = Field(default=5, ge=2, le=20)
    n_estimators: int = Field(default=400, ge=1)
    max_depth: int = Field(default=8, ge=1, le=32)
    learning_rate: float = Field(default=0.1, gt=0.0, le=1.0)
    subsample: float = Field(default=0.8, gt=0.0, le=1.0)
    colsample_bytree: float = Field(default=0.8, gt=0.0, le=1.0)
    early_stopping_rounds: int = Field(default=30, ge=1)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    # OOF fold groups are sub-blocks of this many minutes inside the training partition.
    # Must be <= split.block_minutes; finer groups keep OOF features distributed like the
    # final model's features on unseen blocks (see ADR-0001 amendment).
    oof_group_minutes: int = Field(default=1, ge=1, le=240)


class ModelConfig(_Frozen):
    gru_hidden: int = Field(default=128, ge=1)
    gru_layers: int = Field(default=2, ge=1)
    d_model: int = Field(default=256, ge=1)
    n_heads: int = Field(default=4, ge=1)
    ff_dim: int = Field(default=512, ge=1)
    transformer_layers: int = Field(default=2, ge=1)
    dropout: float = Field(default=0.2, ge=0.0, lt=1.0)

    @model_validator(mode="after")
    def _heads_divide_model_dim(self) -> ModelConfig:
        if self.d_model % self.n_heads != 0:
            raise ValueError(
                f"d_model ({self.d_model}) must be divisible by n_heads ({self.n_heads})"
            )
        return self


class TrainingConfig(_Frozen):
    epochs: int = Field(default=30, ge=1)
    batch_size: int = Field(default=512, ge=1)
    learning_rate: float = Field(default=1e-3, gt=0.0)
    weight_decay: float = Field(default=1e-2, ge=0.0)
    early_stopping_patience: int = Field(default=5, ge=1)
    lr_reduce_factor: float = Field(default=0.5, gt=0.0, lt=1.0)
    lr_reduce_patience: int = Field(default=2, ge=0)
    amp: bool = True
    grad_clip_norm: float = Field(default=1.0, gt=0.0)
    max_train_windows: int | None = Field(default=None, ge=1)
    require_gpu: bool = False  # true on Kaggle: fail fast instead of silently training on CPU


class ServingConfig(_Frozen):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    max_batch: int = Field(default=256, ge=1, le=10_000)
    buffer_ttl_seconds: float = Field(default=300.0, gt=0.0)
    max_buffers: int = Field(default=10_000, ge=1)
    alert_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    low_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class HnidsConfig(_Frozen):
    """Root configuration object. Immutable for the lifetime of a run."""

    schema_version: Literal[1] = 1
    run: RunConfig
    paths: PathsConfig
    data: DataConfig = DataConfig()
    split: SplitConfig = SplitConfig()
    sequence: SequenceConfig = SequenceConfig()
    xgboost: XGBoostConfig = XGBoostConfig()
    model: ModelConfig = ModelConfig()
    training: TrainingConfig = TrainingConfig()
    serving: ServingConfig = ServingConfig()

    @model_validator(mode="after")
    def _oof_groups_within_blocks(self) -> HnidsConfig:
        if self.xgboost.oof_group_minutes > self.split.block_minutes:
            raise ValueError("xgboost.oof_group_minutes must not exceed split.block_minutes")
        return self
