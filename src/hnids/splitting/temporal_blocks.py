"""Class-aware temporal block split (ADR-0001, FR-4).

Each capture is cut into contiguous time blocks. Whole blocks are assigned to train, val or
test, so no window can mix partitions. The rarest classes are placed first, and each block goes
to the split furthest below its target share for that class. This is iterative stratification
at block granularity: short attack bursts still reach every partition when they span enough
blocks. At each boundary where the split changes, the first ``purge_minutes`` of the later block
are dropped, so neighbouring flows cannot straddle two partitions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from hnids.config.schema import SplitConfig
from hnids.errors import DataError, ErrorCode
from hnids.ingestion.schema import LABEL, TIMESTAMP

SPLITS: tuple[str, ...] = ("train", "val", "test")
PURGED = "purged"
SPLIT_COLUMN = "split"
BLOCK_COLUMN = "block_id"
META_COLUMNS: tuple[str, ...] = ("capture_id", "source_row_index", TIMESTAMP, LABEL)
SPLIT_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class SplitResult:
    manifest: pd.DataFrame  # capture_id, source_row_index, block_id, split (incl. "purged")
    support: pd.DataFrame  # rows per (split, class)
    blocks_per_split: dict[str, int]
    manifest_sha256: str


def _block_numbers(meta: pd.DataFrame, block_minutes: int) -> tuple[pd.Series, pd.Series]:
    """Return per-row block number and each row's block start time."""
    width = pd.Timedelta(minutes=block_minutes)
    origin = meta.groupby("capture_id")[TIMESTAMP].transform("min").dt.floor(width)
    number = ((meta[TIMESTAMP] - origin) // width).astype(np.int64)
    return number, origin + number * width


def _assign(counts: pd.DataFrame, ratios: np.ndarray, seed: int) -> pd.Series:
    """Greedy iterative stratification of blocks (rows of ``counts``) into splits."""
    rng = np.random.default_rng(seed)
    order = pd.Series(rng.permutation(len(counts)), index=counts.index)
    class_totals = counts.sum(axis=0)
    row_totals = counts.sum(axis=1)
    total_rows = float(row_totals.sum())
    current = np.zeros((len(SPLITS), counts.shape[1]))
    current_rows = np.zeros(len(SPLITS))
    assignment = pd.Series(pd.NA, index=counts.index, dtype="object")

    for c_pos, cls in sorted(
        enumerate(counts.columns), key=lambda item: (class_totals.iloc[item[0]], str(item[1]))
    ):
        holding = counts.index[(counts[cls] > 0).to_numpy() & assignment.isna().to_numpy()]
        column = counts[cls]
        ranked = sorted(holding, key=lambda b: (-int(column[b]), int(order[b])))
        for block in ranked:
            class_deficit = ratios * class_totals.iloc[c_pos] - current[:, c_pos]
            row_deficit = ratios * total_rows - current_rows
            choice = int(np.lexsort((-row_deficit, -class_deficit))[0])
            assignment[block] = SPLITS[choice]
            current[choice] += counts.loc[block].to_numpy()
            current_rows[choice] += row_totals[block]
    return assignment.astype(str)


def manifest_hash(manifest: pd.DataFrame) -> str:
    """Order-independent SHA-256 of the split manifest content."""
    canonical = manifest.sort_values(["capture_id", "source_row_index"]).reset_index(drop=True)
    digest = hashlib.sha256(f"v{SPLIT_MANIFEST_VERSION}".encode())
    digest.update(pd.util.hash_pandas_object(canonical, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def split_blocks(meta: pd.DataFrame, config: SplitConfig, *, seed: int) -> SplitResult:
    """Assign every row to train/val/test/purged.

    Args:
        meta: One row per flow with ``capture_id``, ``source_row_index``, ``timestamp``,
            ``label`` and the ``config.stratify_by`` column.
        config: Split settings. Stratifying on ``label_raw`` (attack subtype) keeps every
            DoS variant represented in training (ADR-0001).
        seed: Seed for the deterministic tie-breaking order.
    """
    stratify_by = config.stratify_by
    missing = [c for c in (*META_COLUMNS, stratify_by) if c not in meta.columns]
    if missing:
        raise DataError(f"split input missing columns {missing}", code=ErrorCode.SPLIT_INVALID)
    if meta.empty:
        raise DataError("cannot split an empty dataset", code=ErrorCode.DATA_EMPTY)

    frame = meta.loc[:, list(dict.fromkeys((*META_COLUMNS, stratify_by)))].copy()
    block_no, block_start = _block_numbers(frame, config.block_minutes)
    frame["block_no"] = block_no
    frame[BLOCK_COLUMN] = frame["capture_id"].astype(str) + ":" + block_no.astype(str)

    counts = frame.groupby([BLOCK_COLUMN, stratify_by]).size().unstack(fill_value=0).sort_index()
    ratios = np.array([config.train_ratio, config.val_ratio, config.test_ratio])
    block_split = _assign(counts, ratios, seed)
    frame[SPLIT_COLUMN] = frame[BLOCK_COLUMN].map(block_split)

    # Purge the start of a block whose immediately preceding block went to a different split.
    if config.purge_minutes > 0:
        blocks = (
            frame.groupby(BLOCK_COLUMN)
            .agg(capture_id=("capture_id", "first"), block_no=("block_no", "first"))
            .assign(split=block_split)
        )
        prev = blocks.assign(block_no=blocks["block_no"] + 1).set_index(["capture_id", "block_no"])
        keyed = blocks.set_index(["capture_id", "block_no"])
        prev_split = prev["split"].reindex(keyed.index)
        boundary = (prev_split.notna() & (prev_split != keyed["split"])).to_numpy()
        boundary_blocks = set(blocks.index[boundary])
        in_boundary = frame[BLOCK_COLUMN].isin(boundary_blocks)
        early = frame[TIMESTAMP] < block_start + pd.Timedelta(minutes=config.purge_minutes)
        frame.loc[in_boundary & early, SPLIT_COLUMN] = PURGED

    manifest = frame.loc[:, ["capture_id", "source_row_index", BLOCK_COLUMN, SPLIT_COLUMN]]
    manifest = manifest.reset_index(drop=True)
    kept = frame[frame[SPLIT_COLUMN] != PURGED]
    support = (
        kept.groupby([SPLIT_COLUMN, LABEL])
        .size()
        .unstack(fill_value=0)
        .reindex(list(SPLITS), fill_value=0)
    )
    blocks_per_split = {s: int(block_split.eq(s).sum()) for s in SPLITS}
    return SplitResult(
        manifest=manifest,
        support=support,
        blocks_per_split=blocks_per_split,
        manifest_sha256=manifest_hash(manifest),
    )
