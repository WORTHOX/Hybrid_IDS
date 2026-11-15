"""Bounded-memory ingestion: CSV day-files -> per-capture parquet + quarantine + quality report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from hnids.config.schema import DataConfig
from hnids.errors import DataError, ErrorCode
from hnids.ingestion.sanitize import ChunkStats, sanitize_chunk
from hnids.ingestion.schema import (
    FEATURE_SCHEMA_VERSION,
    FLOW_ID,
    LABEL,
    MODEL_FEATURE_COLUMNS,
    REQUIRED_SOURCE_COLUMNS,
    TIMESTAMP,
    normalize_columns,
)
from hnids.ingestion.taxonomy import TAXONOMY_VERSION
from hnids.logging import get_logger
from hnids.security.integrity import sha256_file
from hnids.security.paths import AuthorizedRoots

CAPTURE_SUFFIXES = (".pcap_ISCX.csv", ".csv")

ACCEPTED_SCHEMA = pa.schema(
    [
        ("capture_id", pa.string()),
        ("source_row_index", pa.int64()),
        (TIMESTAMP, pa.timestamp("ns")),
        (FLOW_ID, pa.string()),
        ("label_raw", pa.string()),
        (LABEL, pa.string()),
        *[(name, pa.float32()) for name in MODEL_FEATURE_COLUMNS],
    ]
)

_log = get_logger("ingestion")


class CaptureQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    capture_id: str
    source_file: str
    source_sha256: str
    rows_read: int
    empty_rows: int
    accepted: int
    quarantined: dict[str, int]
    rate_nonfinite: dict[str, int]
    pm_fixed: int
    duplicate_column_mismatches: int
    duplicate_rows: int
    extra_columns: list[str]
    label_counts_raw: dict[str, int]
    label_counts: dict[str, int]
    timestamp_min: str | None
    timestamp_max: str | None
    hour_histogram: dict[str, int]
    out_of_order_rows: int


class QualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_schema_version: int = FEATURE_SCHEMA_VERSION
    taxonomy_version: int = TAXONOMY_VERSION
    captures: list[CaptureQuality]

    @property
    def total_accepted(self) -> int:
        return sum(c.accepted for c in self.captures)


def capture_id_for(path: Path) -> str:
    name = path.name
    for suffix in CAPTURE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def discover_sources(data: DataConfig, roots: AuthorizedRoots) -> list[Path]:
    """Find source CSVs under read roots, resolved and containment-checked."""
    found: set[Path] = set()
    for root in roots.read_roots:
        if root.is_dir():
            for candidate in root.glob(data.source_glob):
                if candidate.is_file():
                    found.add(roots.resolve(candidate, mode="read", must_exist=True))
    sources = sorted(found, key=lambda p: p.name)
    if not sources:
        raise DataError(
            f"no source files match {data.source_glob!r} under read roots",
            code=ErrorCode.DATA_EMPTY,
            details={"roots": [str(r) for r in roots.read_roots]},
        )
    names = [capture_id_for(p) for p in sources]
    duplicates = sorted(n for n, c in Counter(names).items() if c > 1)
    if duplicates:
        raise DataError(
            f"capture ids are not unique: {duplicates}", details={"captures": duplicates}
        )
    return sources[: data.max_files] if data.max_files else sources


def _read_header(path: Path, encoding: str) -> list[str]:
    with path.open("r", encoding=encoding, newline="") as handle:
        header = handle.readline().rstrip("\r\n")
    return normalize_columns(header.split(","))


def _counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.value_counts().items()}


def _row_hashes(frame: pd.DataFrame) -> np.ndarray:
    cols = [LABEL, *MODEL_FEATURE_COLUMNS]
    return pd.util.hash_pandas_object(frame[cols], index=False).to_numpy()


def ingest_capture(
    path: Path,
    *,
    data: DataConfig,
    roots: AuthorizedRoots,
    accepted_dir: Path,
    quarantine_dir: Path,
) -> CaptureQuality:
    """Stream one CSV in chunks into ``<accepted_dir>/<capture>.parquet`` (+ quarantine)."""
    source = roots.resolve(path, mode="read", must_exist=True)
    capture_id = capture_id_for(source)
    columns = _read_header(source, data.encoding)
    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in columns]
    if missing:
        raise DataError(
            f"{source.name}: required columns missing: {missing}",
            code=ErrorCode.SCHEMA_MISMATCH,
            details={"capture_id": capture_id, "missing": missing},
        )
    known = set(REQUIRED_SOURCE_COLUMNS)
    extra = [c for c in columns if c not in known and "__dup" not in c]

    accepted_path = roots.resolve(accepted_dir / f"{capture_id}.parquet", mode="write")
    quarantine_path = roots.resolve(quarantine_dir / f"{capture_id}.parquet", mode="write")
    quarantine_schema = pa.schema(
        [
            ("capture_id", pa.string()),
            ("source_row_index", pa.int64()),
            ("reason_code", pa.string()),
        ]
        + [(c, pa.string()) for c in columns]
    )

    stats = ChunkStats()
    raw_labels: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    seen_hashes: set[int] = set()
    duplicate_rows = 0
    ts_min: pd.Timestamp | None = None
    ts_max: pd.Timestamp | None = None
    out_of_order = 0
    last_ts: pd.Timestamp | None = None
    quarantine_writer: pq.ParquetWriter | None = None

    reader = pd.read_csv(
        source,
        encoding=data.encoding,
        header=0,
        names=columns,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        chunksize=data.chunk_rows,
        nrows=data.max_rows_per_file,
    )
    with pq.ParquetWriter(accepted_path, ACCEPTED_SCHEMA) as writer:
        offset = 0
        for chunk in reader:
            result = sanitize_chunk(chunk, capture_id=capture_id, first_row_index=offset)
            offset += len(chunk)
            stats.merge(result.stats)
            frame = result.accepted
            if len(frame):
                writer.write_table(
                    pa.Table.from_pandas(frame, schema=ACCEPTED_SCHEMA, preserve_index=False)
                )
                raw_labels.update(_counts(frame["label_raw"]))
                classes.update(_counts(frame[LABEL]))
                hours.update(_counts(frame[TIMESTAMP].dt.hour))
                for h in _row_hashes(frame):
                    key = int(h)
                    if key in seen_hashes:
                        duplicate_rows += 1
                    else:
                        seen_hashes.add(key)
                ts = frame[TIMESTAMP]
                ts_min = ts.min() if ts_min is None else min(ts_min, ts.min())
                ts_max = ts.max() if ts_max is None else max(ts_max, ts.max())
                previous = ts.shift(1)
                if last_ts is not None:
                    previous.iloc[0] = last_ts
                out_of_order += int((ts < previous).sum())
                last_ts = ts.iloc[-1]
            if len(result.quarantined):
                if quarantine_writer is None:
                    quarantine_writer = pq.ParquetWriter(quarantine_path, quarantine_schema)
                quarantine_writer.write_table(
                    pa.Table.from_pandas(
                        result.quarantined, schema=quarantine_schema, preserve_index=False
                    )
                )
    if quarantine_writer is not None:
        quarantine_writer.close()

    quality = CaptureQuality(
        capture_id=capture_id,
        source_file=source.name,
        source_sha256=sha256_file(source),
        rows_read=stats.rows_read,
        empty_rows=stats.empty_rows,
        accepted=stats.accepted,
        quarantined=dict(sorted(stats.quarantined.items())),
        rate_nonfinite=stats.rate_nonfinite,
        pm_fixed=stats.pm_fixed,
        duplicate_column_mismatches=stats.duplicate_column_mismatches,
        duplicate_rows=duplicate_rows,
        extra_columns=extra,
        label_counts_raw=dict(raw_labels.most_common()),
        label_counts=dict(classes.most_common()),
        timestamp_min=None if ts_min is None else ts_min.isoformat(),
        timestamp_max=None if ts_max is None else ts_max.isoformat(),
        hour_histogram=dict(sorted(hours.items(), key=lambda kv: int(kv[0]))),
        out_of_order_rows=out_of_order,
    )
    _log.info(
        "capture_ingested",
        capture_id=capture_id,
        accepted=quality.accepted,
        empty=quality.empty_rows,
        quarantined=quality.quarantined,
    )
    return quality


def ingest_all(
    data: DataConfig, roots: AuthorizedRoots, *, accepted_dir: Path, quarantine_dir: Path
) -> QualityReport:
    """Ingest every discovered capture and write ``quality_report.json`` next to the parquet."""
    accepted_dir = roots.ensure_dir(accepted_dir)
    quarantine_dir = roots.ensure_dir(quarantine_dir)
    captures = [
        ingest_capture(
            path, data=data, roots=roots, accepted_dir=accepted_dir, quarantine_dir=quarantine_dir
        )
        for path in discover_sources(data, roots)
    ]
    report = QualityReport(captures=captures)
    if report.total_accepted == 0:
        raise DataError("no rows survived ingestion", code=ErrorCode.DATA_EMPTY)
    target = roots.resolve(accepted_dir / "quality_report.json", mode="write")
    target.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def load_capture(path: Path, roots: AuthorizedRoots) -> pd.DataFrame:
    """Load an ingested capture parquet sorted chronologically (ties keep source order)."""
    frame = pd.read_parquet(roots.resolve(path, mode="read", must_exist=True))
    return frame.sort_values([TIMESTAMP, "source_row_index"], kind="stable").reset_index(drop=True)
