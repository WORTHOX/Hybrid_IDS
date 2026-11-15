"""Assemble leakage-safe train/val/test arrays from ingested captures.

Order of operations (each step only consumes what the previous one allows):
1. Load captures (sorted chronologically), then split by whole time blocks (ADR-0001).
2. Sort each split so every block is one contiguous run, then build window indices.
3. Fit the scaler on **training rows only** and apply it to all splits.
4. Audit cross-split exact duplicates (an inherent CIC-IDS2017 property, reported, not hidden).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from hnids.config.schema import HnidsConfig
from hnids.errors import DataError, ErrorCode
from hnids.ingestion.pipeline import load_capture
from hnids.ingestion.schema import FLOW_ID, LABEL, MODEL_FEATURE_COLUMNS, TIMESTAMP
from hnids.ingestion.taxonomy import class_index
from hnids.logging import get_logger
from hnids.preprocessing.scaler import FeatureScaler
from hnids.security.paths import AuthorizedRoots
from hnids.sequencing.windows import WindowIndex, build_window_index
from hnids.splitting.temporal_blocks import (
    BLOCK_COLUMN,
    PURGED,
    SPLIT_COLUMN,
    SPLITS,
    SplitResult,
    split_blocks,
)

_log = get_logger("training.prepare")


@dataclass
class SplitArrays:
    name: str
    features: np.ndarray  # (N, F) scaled float32
    labels: np.ndarray  # (N,) int64 class index
    groups: np.ndarray  # (N,) int64 block code, contiguous runs
    capture_ids: np.ndarray  # (N,) object
    timestamps: np.ndarray  # (N,) datetime64[ns]
    flow_ids: np.ndarray  # (N,) object
    windows: WindowIndex
    probabilities: np.ndarray | None = None  # (N, C) XGBoost probabilities, set later

    def fused(self) -> np.ndarray:
        if self.probabilities is None:
            raise DataError("XGBoost probabilities not attached", code=ErrorCode.MODEL_NOT_READY)
        return np.concatenate([self.features, self.probabilities], axis=1).astype(np.float32)

    def sub_block_groups(self, minutes: int) -> np.ndarray:
        """Group codes for (block, ``minutes``-wide time bucket); never crosses a block."""
        bucket = pd.Series(self.timestamps).dt.floor(f"{minutes}min").astype("int64").to_numpy()
        keys = pd.DataFrame({"g": self.groups, "b": bucket})
        codes: np.ndarray = keys.groupby(["g", "b"], sort=False).ngroup().to_numpy(np.int64)
        return codes

    @property
    def window_labels(self) -> np.ndarray:
        selected: np.ndarray = self.labels[self.windows.ends]
        return selected


@dataclass
class PreparedData:
    scaler: FeatureScaler
    splits: dict[str, SplitArrays]
    split_result: SplitResult
    duplicate_audit: dict[str, float] = field(default_factory=dict)


def load_ingested(ingested_dir: Path, roots: AuthorizedRoots) -> pd.DataFrame:
    directory = roots.resolve(ingested_dir, mode="read", must_exist=True)
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise DataError(f"no ingested captures in {directory}", code=ErrorCode.DATA_EMPTY)
    return pd.concat([load_capture(f, roots) for f in files], ignore_index=True)


def _row_hashes(frame: pd.DataFrame) -> np.ndarray:
    return pd.util.hash_pandas_object(
        frame[[LABEL, *MODEL_FEATURE_COLUMNS]], index=False
    ).to_numpy()


def prepare(frame: pd.DataFrame, config: HnidsConfig) -> PreparedData:
    """Split, sort, window and scale an ingested dataset."""
    result = split_blocks(frame, config.split, seed=config.run.seed)
    manifest = result.manifest
    aligned = (manifest["capture_id"].to_numpy() == frame["capture_id"].to_numpy()).all() and (
        manifest["source_row_index"].to_numpy() == frame["source_row_index"].to_numpy()
    ).all()
    if not aligned:
        raise DataError(
            "split manifest is not aligned with input rows", code=ErrorCode.SPLIT_INVALID
        )

    frame = frame.assign(
        **{
            SPLIT_COLUMN: manifest[SPLIT_COLUMN].to_numpy(),
            BLOCK_COLUMN: manifest[BLOCK_COLUMN].to_numpy(),
        }
    )
    frame = frame[frame[SPLIT_COLUMN] != PURGED]
    index = class_index()
    raw: dict[str, tuple[pd.DataFrame, np.ndarray]] = {}
    for name in SPLITS:
        part = frame[frame[SPLIT_COLUMN] == name].sort_values(
            ["capture_id", TIMESTAMP, "source_row_index"], kind="stable"
        )
        raw[name] = (part, part[list(MODEL_FEATURE_COLUMNS)].to_numpy(dtype=np.float32))

    train_frame, train_x = raw["train"]
    if len(train_frame) == 0:
        raise DataError("training split is empty", code=ErrorCode.DATA_EMPTY)
    scaler = FeatureScaler.fit(train_x, MODEL_FEATURE_COLUMNS)

    splits: dict[str, SplitArrays] = {}
    for name, (part, matrix) in raw.items():
        groups = pd.factorize(part[BLOCK_COLUMN])[0].astype(np.int64)
        windows = build_window_index(groups, config.sequence.length)
        splits[name] = SplitArrays(
            name=name,
            features=scaler.transform_array(matrix) if len(matrix) else matrix,
            labels=part[LABEL].map(index).to_numpy(dtype=np.int64),
            groups=groups,
            capture_ids=part["capture_id"].astype(str).to_numpy(dtype=object),
            timestamps=part[TIMESTAMP].to_numpy(dtype="datetime64[ns]"),
            flow_ids=part[FLOW_ID].astype(str).to_numpy(dtype=object),
            windows=windows,
        )
        _log.info("split_prepared", split=name, rows=len(part), **windows.stats.__dict__)

    train_hashes = np.unique(_row_hashes(train_frame))
    audit = {
        f"{name}_rows_identical_to_a_train_row": float(
            np.isin(_row_hashes(raw[name][0]), train_hashes).mean()
        )
        for name in ("val", "test")
        if len(raw[name][0])
    }
    return PreparedData(scaler=scaler, splits=splits, split_result=result, duplicate_audit=audit)
