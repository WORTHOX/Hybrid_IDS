from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from hnids.config import HnidsConfig
from hnids.errors import DataError, ErrorCode
from hnids.ingestion.pipeline import (
    capture_id_for,
    discover_sources,
    ingest_all,
    ingest_capture,
    load_capture,
)
from hnids.ingestion.schema import MODEL_FEATURE_COLUMNS, RATE_NONFINITE
from hnids.security import AuthorizedRoots
from tests.synthetic import WEB_XSS, Flow, write_capture

T0 = datetime(2017, 7, 6, 9, 0)


@pytest.fixture
def roots(config: HnidsConfig) -> AuthorizedRoots:
    return AuthorizedRoots.from_config(config.paths)


def _ingest(path: Path, config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]):  # type: ignore[no-untyped-def]
    return ingest_capture(
        path,
        data=config.data,
        roots=roots,
        accepted_dir=roots.ensure_dir(workspace["artifacts"] / "ingested"),
        quarantine_dir=roots.ensure_dir(workspace["quarantine"]),
    )


def test_quirky_file_ingests_with_explicit_policy(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    flows = [
        Flow(T0, "BENIGN"),
        Flow(T0, WEB_XSS),
        Flow(T0, "BENIGN", rate="Infinity"),
        Flow(T0, "BENIGN", rate="NaN"),
        Flow(T0, ""),  # missing label
        Flow(T0, "Ransomware"),  # unknown label
        Flow(T0, "BENIGN", timestamp_text="not a time"),
        Flow(T0, "BENIGN", feature_override=("flow_duration", "abc")),
        Flow(T0, "BENIGN", feature_override=("flow_iat_mean", "")),
        Flow(T0, "BENIGN", feature_override=("flow_iat_max", "Infinity")),
        Flow(datetime(2017, 7, 6, 15, 5), "DDoS"),  # rendered as bare "3:05"
    ]
    path = write_capture(workspace["data"] / "Thu-Test.pcap_ISCX.csv", flows, empty_rows=4)
    quality = _ingest(path, config, roots, workspace)

    assert quality.capture_id == "Thu-Test"
    assert quality.rows_read == 15
    assert quality.empty_rows == 4
    assert quality.accepted == 5
    assert quality.quarantined == {
        "INVALID_NUMBER": 1,
        "INVALID_TIMESTAMP": 1,
        "MISSING_FIELD": 1,
        "MISSING_LABEL": 1,
        "NONFINITE_VALUE": 1,
        "UNKNOWN_LABEL": 1,
    }
    assert quality.rate_nonfinite["flow_bytes_s"] == 2
    assert quality.pm_fixed >= 1
    assert quality.duplicate_column_mismatches == 0
    assert quality.label_counts == {"BENIGN": 3, "WebAttack": 1, "DDoS": 1}

    accepted = pd.read_parquet(workspace["artifacts"] / "ingested" / "Thu-Test.parquet")
    assert list(accepted.columns[-len(MODEL_FEATURE_COLUMNS) :]) == list(MODEL_FEATURE_COLUMNS)
    assert accepted[RATE_NONFINITE].sum() == 2
    assert accepted.loc[accepted[RATE_NONFINITE] == 1, "flow_bytes_s"].eq(0).all()
    assert accepted["timestamp"].dt.hour.max() == 15
    assert "source_ip" not in accepted.columns  # identifiers minimized (ADR-0003)

    quarantine = pd.read_parquet(workspace["quarantine"] / "Thu-Test.parquet")
    assert len(quarantine) == 6
    assert set(quarantine["reason_code"]) == set(quality.quarantined)


def test_missing_required_column_is_schema_error(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    path = workspace["data"] / "Broken.csv"
    path.write_text("Flow ID, Source IP, Label\r\nx,1.1.1.1,BENIGN\r\n", encoding="cp1252")
    with pytest.raises(DataError) as info:
        _ingest(path, config, roots, workspace)
    assert info.value.code is ErrorCode.SCHEMA_MISMATCH


def test_ingest_refuses_source_outside_roots(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path], tmp_path: Path
) -> None:
    outside = write_capture(tmp_path / "Outside.pcap_ISCX.csv", [Flow(T0, "BENIGN")])
    from hnids.errors import PathSecurityError

    with pytest.raises(PathSecurityError):
        _ingest(outside, config, roots, workspace)


def test_duplicate_column_disagreement_is_counted(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    path = write_capture(workspace["data"] / "Dup.pcap_ISCX.csv", [Flow(T0, "BENIGN")] * 2)
    text = path.read_bytes().decode("cp1252").split("\r\n")
    cells = text[1].split(",")
    dup_position = [
        i for i, h in enumerate(text[0].split(",")) if h.strip() == "fwd_header_length"
    ][1]
    cells[dup_position] = "999999"
    text[1] = ",".join(cells)
    path.write_bytes("\r\n".join(text).encode("cp1252"))
    assert _ingest(path, config, roots, workspace).duplicate_column_mismatches == 1


def test_chunked_reading_matches_single_pass(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    flows = [Flow(T0, "BENIGN" if i % 3 else "DoS Hulk") for i in range(2500)]
    path = write_capture(workspace["data"] / "Big.pcap_ISCX.csv", flows)
    small = config.data.model_copy(update={"chunk_rows": 1000})
    quality = ingest_capture(
        path,
        data=small,
        roots=roots,
        accepted_dir=roots.ensure_dir(workspace["artifacts"] / "ingested"),
        quarantine_dir=roots.ensure_dir(workspace["quarantine"]),
    )
    frame = pd.read_parquet(workspace["artifacts"] / "ingested" / "Big.parquet")
    assert quality.accepted == len(frame) == 2500
    assert frame["source_row_index"].tolist() == list(range(2500))


def test_max_rows_per_file_limits_read(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    path = write_capture(workspace["data"] / "Lim.pcap_ISCX.csv", [Flow(T0, "BENIGN")] * 50)
    limited = config.data.model_copy(update={"max_rows_per_file": 10})
    quality = ingest_capture(
        path,
        data=limited,
        roots=roots,
        accepted_dir=roots.ensure_dir(workspace["artifacts"] / "ingested"),
        quarantine_dir=roots.ensure_dir(workspace["quarantine"]),
    )
    assert quality.rows_read == 10


def test_load_capture_sorts_chronologically_with_stable_ties(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    flows = [
        Flow(datetime(2017, 7, 6, 10, 5), "BENIGN"),
        Flow(datetime(2017, 7, 6, 9, 0), "BENIGN"),
        Flow(datetime(2017, 7, 6, 9, 0), "DDoS"),
    ]
    path = write_capture(workspace["data"] / "Ooo.pcap_ISCX.csv", flows)
    quality = _ingest(path, config, roots, workspace)
    assert quality.out_of_order_rows == 1
    frame = load_capture(workspace["artifacts"] / "ingested" / "Ooo.parquet", roots)
    assert frame["source_row_index"].tolist() == [1, 2, 0]


def test_ingest_all_discovers_and_reports(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    write_capture(workspace["data"] / "A.pcap_ISCX.csv", [Flow(T0, "BENIGN")] * 3)
    write_capture(workspace["data"] / "B.pcap_ISCX.csv", [Flow(T0, "PortScan")] * 2)
    report = ingest_all(
        config.data,
        roots,
        accepted_dir=workspace["artifacts"] / "ingested",
        quarantine_dir=workspace["quarantine"],
    )
    assert [c.capture_id for c in report.captures] == ["A", "B"]
    assert report.total_accepted == 5
    saved = json.loads((workspace["artifacts"] / "ingested" / "quality_report.json").read_text())
    assert saved["feature_schema_version"] == 1


def test_discover_with_no_sources_fails(config: HnidsConfig, roots: AuthorizedRoots) -> None:
    with pytest.raises(DataError) as info:
        discover_sources(config.data, roots)
    assert info.value.code is ErrorCode.DATA_EMPTY


def test_discover_respects_max_files(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    for name in ("A", "B", "C"):
        write_capture(workspace["data"] / f"{name}.pcap_ISCX.csv", [Flow(T0, "BENIGN")])
    limited = config.data.model_copy(update={"max_files": 2})
    assert [p.name for p in discover_sources(limited, roots)] == [
        "A.pcap_ISCX.csv",
        "B.pcap_ISCX.csv",
    ]


def test_ingest_all_with_only_bad_rows_fails(
    config: HnidsConfig, roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    write_capture(workspace["data"] / "Bad.pcap_ISCX.csv", [Flow(T0, "Ransomware")])
    with pytest.raises(DataError) as info:
        ingest_all(
            config.data,
            roots,
            accepted_dir=workspace["artifacts"] / "ingested",
            quarantine_dir=workspace["quarantine"],
        )
    assert info.value.code is ErrorCode.DATA_EMPTY


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Monday-WorkingHours.pcap_ISCX.csv", "Monday-WorkingHours"), ("x.csv", "x"), ("y.txt", "y")],
)
def test_capture_id_for(name: str, expected: str) -> None:
    assert capture_id_for(Path(name)) == expected
