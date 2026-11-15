"""Per-chunk validation and sanitation with explicit, per-feature policy (FR-3).

Policy:
- Fully empty rows are dropped and counted (CSV padding, not telemetry).
- Rate features (``RATE_FEATURES``) that are missing or non-finite are zero-filled and flagged
  in ``rate_nonfinite``: CICFlowMeter emits ``Infinity``/``NaN`` for zero-duration flows.
- Every other numeric feature must be present, numeric and finite, otherwise the row is
  quarantined with a reason code.
- Labels must map into the taxonomy; timestamps must parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from hnids.ingestion.schema import (
    DUPLICATE_SUFFIX,
    FLOW_ID,
    LABEL,
    RATE_FEATURES,
    RATE_NONFINITE,
    RAW_FEATURE_COLUMNS,
    TIMESTAMP,
)
from hnids.ingestion.taxonomy import canonical_raw_label, map_label
from hnids.ingestion.timestamps import parse_timestamps


class QuarantineReason(StrEnum):
    MISSING_LABEL = "MISSING_LABEL"
    UNKNOWN_LABEL = "UNKNOWN_LABEL"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_NUMBER = "INVALID_NUMBER"
    NONFINITE_VALUE = "NONFINITE_VALUE"


@dataclass
class ChunkStats:
    rows_read: int = 0
    empty_rows: int = 0
    accepted: int = 0
    pm_fixed: int = 0
    duplicate_column_mismatches: int = 0
    quarantined: dict[str, int] = field(default_factory=dict)
    rate_nonfinite: dict[str, int] = field(default_factory=dict)

    def merge(self, other: ChunkStats) -> None:
        self.rows_read += other.rows_read
        self.empty_rows += other.empty_rows
        self.accepted += other.accepted
        self.pm_fixed += other.pm_fixed
        self.duplicate_column_mismatches += other.duplicate_column_mismatches
        for source, target in (
            (other.quarantined, self.quarantined),
            (other.rate_nonfinite, self.rate_nonfinite),
        ):
            for key, value in source.items():
                target[key] = target.get(key, 0) + value


@dataclass
class SanitizedChunk:
    accepted: pd.DataFrame
    quarantined: pd.DataFrame
    stats: ChunkStats


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype("string").str.strip() == "")


def sanitize_chunk(raw: pd.DataFrame, *, capture_id: str, first_row_index: int) -> SanitizedChunk:
    """Validate one chunk of string-typed, column-normalized source rows.

    Args:
        raw: Chunk with normalized column names, all values as strings.
        capture_id: Stable identifier of the source capture.
        first_row_index: Zero-based source row index of the chunk's first row.
    """
    stats = ChunkStats(rows_read=len(raw))
    source_index = pd.RangeIndex(first_row_index, first_row_index + len(raw))
    raw = raw.set_axis(source_index, axis=0)

    empty = raw.apply(_blank).all(axis=1)
    stats.empty_rows = int(empty.sum())
    rows = raw.loc[~empty]

    reason = pd.Series(pd.NA, index=rows.index, dtype="string")

    def flag(mask: pd.Series, code: QuarantineReason) -> None:
        nonlocal reason
        hit = mask & reason.isna()
        reason = reason.mask(hit, code.value)

    # Labels
    label_blank = _blank(rows[LABEL])
    flag(label_blank, QuarantineReason.MISSING_LABEL)
    canonical = rows[LABEL].fillna("").astype(str).map(canonical_raw_label)
    mapped = rows[LABEL].fillna("").astype(str).map(map_label)
    flag(mapped.isna() & ~label_blank, QuarantineReason.UNKNOWN_LABEL)

    # Timestamps
    timestamps, pm_fixed = parse_timestamps(rows[TIMESTAMP])
    flag(timestamps.isna(), QuarantineReason.INVALID_TIMESTAMP)
    stats.pm_fixed = int(pm_fixed.sum())

    # Numeric features
    features = pd.DataFrame(index=rows.index)
    rate_flag = pd.Series(False, index=rows.index)
    for column in RAW_FEATURE_COLUMNS:
        text = rows[column]
        blank = _blank(text)
        values = pd.to_numeric(text, errors="coerce")
        invalid = (
            values.isna() & ~blank & ~text.astype("string").str.strip().str.lower().isin(["nan"])
        )
        nonfinite = values.isna() | np.isinf(values)
        if column in RATE_FEATURES:
            hit = nonfinite & ~invalid
            stats.rate_nonfinite[column] = int(hit.sum())
            rate_flag |= hit
            values = values.mask(hit, 0.0)
            flag(invalid, QuarantineReason.INVALID_NUMBER)
        else:
            flag(blank, QuarantineReason.MISSING_FIELD)
            flag(invalid, QuarantineReason.INVALID_NUMBER)
            flag(nonfinite & ~blank & ~invalid, QuarantineReason.NONFINITE_VALUE)
        features[column] = values

    # Duplicated source columns must agree with the original before being discarded.
    for column in [c for c in rows.columns if DUPLICATE_SUFFIX in c]:
        original = column.split(DUPLICATE_SUFFIX)[0]
        if original in features:
            dup_values = pd.to_numeric(rows[column], errors="coerce")
            mismatch = ~(
                dup_values.eq(features[original]) | (dup_values.isna() & features[original].isna())
            )
            stats.duplicate_column_mismatches += int(mismatch.sum())

    bad = reason.notna()
    for code, count in reason[bad].value_counts().items():
        stats.quarantined[str(code)] = int(count)

    good = ~bad
    accepted = pd.DataFrame(
        {
            "capture_id": capture_id,
            "source_row_index": rows.index[good].to_numpy(dtype=np.int64),
            TIMESTAMP: timestamps[good].to_numpy(),
            FLOW_ID: rows.loc[good, "flow_id"].astype(str).to_numpy(),
            "label_raw": canonical[good].to_numpy(),
            LABEL: mapped[good].astype(str).to_numpy(),
        }
    )
    feature_block = features.loc[good].astype(np.float32).reset_index(drop=True)
    feature_block[RATE_NONFINITE] = rate_flag[good].astype(np.float32).to_numpy()
    accepted = pd.concat([accepted, feature_block], axis=1)
    stats.accepted = len(accepted)

    quarantined = rows.loc[bad].astype("string").copy()
    quarantined.insert(0, "reason_code", reason[bad])
    quarantined.insert(0, "source_row_index", rows.index[bad].to_numpy(dtype=np.int64))
    quarantined.insert(0, "capture_id", capture_id)
    return SanitizedChunk(
        accepted=accepted, quarantined=quarantined.reset_index(drop=True), stats=stats
    )
