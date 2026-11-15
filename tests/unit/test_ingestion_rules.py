from __future__ import annotations

import pandas as pd
import pytest

from hnids.ingestion.schema import normalize_column, normalize_columns
from hnids.ingestion.taxonomy import CLASSES, canonical_raw_label, class_index, map_label
from hnids.ingestion.timestamps import parse_timestamps


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Flow Bytes/s", "flow_bytes_s"),
        ("Init_Win_bytes_forward", "init_win_bytes_forward"),
        (" Down/Up Ratio", "down_up_ratio"),
        ("Flow ID", "flow_id"),
        ("flow_id", "flow_id"),
    ],
)
def test_normalize_column(raw: str, expected: str) -> None:
    assert normalize_column(raw) == expected


def test_duplicate_headers_disambiguated() -> None:
    names = normalize_columns(["A", " Fwd Header Length", "B", " Fwd Header Length"])
    assert names == ["a", "fwd_header_length", "b", "fwd_header_length__dup1"]


@pytest.mark.parametrize(
    ("raw", "cls"),
    [
        ("BENIGN", "BENIGN"),
        ("Web Attack \x96 Brute Force", "WebAttack"),  # cp1252 en-dash decoded as latin-1
        ("Web Attack " + chr(0x2013) + " XSS", "WebAttack"),
        ("Web Attack " + chr(0xFFFD) + " Sql Injection", "WebAttack"),  # replacement character
        ("Web Attack - Sql Injection", "WebAttack"),
        ("  DoS   Hulk ", "DoS"),
        ("DoS slowloris", "DoS"),
        ("FTP-Patator", "BruteForce"),
        ("Heartbleed", "Rare"),
        ("Infiltration", "Rare"),
        ("PortScan", "PortScan"),
    ],
)
def test_label_mapping(raw: str, cls: str) -> None:
    assert map_label(raw) == cls


def test_unknown_label_maps_to_none() -> None:
    assert map_label("Ransomware") is None
    assert canonical_raw_label("Web Attack\x96XSS") == "web attack - xss"


def test_class_index_covers_taxonomy() -> None:
    idx = class_index()
    assert list(idx) == list(CLASSES)
    assert idx["BENIGN"] == 0


def _parse(values: list[str]) -> tuple[list[str], list[bool]]:
    parsed, fixed = parse_timestamps(pd.Series(values, dtype="string"))
    return [str(v) for v in parsed], fixed.tolist()


def test_timestamps_24h_morning_unchanged() -> None:
    parsed, fixed = _parse(["6/7/2017 8:59", "6/7/2017 12:05"])
    assert parsed == ["2017-07-06 08:59:00", "2017-07-06 12:05:00"]
    assert fixed == [False, False]


def test_timestamps_bare_afternoon_shifted_to_pm() -> None:
    parsed, fixed = _parse(["7/7/2017 3:30", "7/7/2017 1:00"])
    assert parsed == ["2017-07-07 15:30:00", "2017-07-07 13:00:00"]
    assert fixed == [True, True]


def test_timestamps_with_seconds_day_first() -> None:
    parsed, _ = _parse(["03/07/2017 08:55:58"])
    assert parsed == ["2017-07-03 08:55:58"]


def test_timestamps_explicit_meridiem() -> None:
    parsed, fixed = _parse(["5/7/2017 3:10 PM", "5/7/2017 12:01 AM", "5/7/2017 9:00 am"])
    assert parsed == ["2017-07-05 15:10:00", "2017-07-05 00:01:00", "2017-07-05 09:00:00"]
    assert fixed == [False, False, False]


@pytest.mark.parametrize("bad", ["", "yesterday", "2017-07-05 10:00", "32/7/2017 9:00", "5/7/2017"])
def test_unparseable_timestamps_are_nat(bad: str) -> None:
    parsed, fixed = _parse([bad])
    assert parsed == ["NaT"]
    assert fixed == [False]
