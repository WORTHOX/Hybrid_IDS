from __future__ import annotations

import pandas as pd
import pytest

from hnids.config.schema import SplitConfig
from hnids.errors import DataError, ErrorCode
from hnids.splitting.temporal_blocks import PURGED, SPLITS, split_blocks


def _meta(
    minutes: int, per_minute: int, attacks: dict[str, tuple[int, int]], capture: str = "c1"
) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2017-07-05 09:00")
    i = 0
    for m in range(minutes):
        label = next((name for name, (lo, hi) in attacks.items() if lo <= m < hi), "BENIGN")
        for _ in range(per_minute):
            rows.append(
                {
                    "capture_id": capture,
                    "source_row_index": i,
                    "timestamp": start + pd.Timedelta(minutes=m),
                    "label": label,
                    "label_raw": label.lower(),
                }
            )
            i += 1
    return pd.DataFrame(rows)


CFG = SplitConfig(block_minutes=5, purge_minutes=1)


def test_every_row_assigned_and_blocks_are_whole() -> None:
    meta = _meta(400, 3, {"DoS": (100, 200)})
    result = split_blocks(meta, CFG, seed=0)
    m = result.manifest
    assert len(m) == len(meta)
    assert set(m["split"]) <= {*SPLITS, PURGED}
    non_purged = m[m["split"] != PURGED]
    assert non_purged.groupby("block_id")["split"].nunique().max() == 1


def test_ratios_are_approximately_respected() -> None:
    meta = _meta(2000, 2, {"DoS": (300, 700), "PortScan": (1200, 1500)})
    result = split_blocks(meta, SplitConfig(block_minutes=5, purge_minutes=0), seed=1)
    shares = result.manifest["split"].value_counts(normalize=True)
    assert shares["train"] == pytest.approx(0.70, abs=0.03)
    assert shares["val"] == pytest.approx(0.15, abs=0.03)
    assert shares["test"] == pytest.approx(0.15, abs=0.03)


def test_short_attack_reaches_every_split() -> None:
    # A 30-minute attack spans 6 five-minute blocks: stratification must spread it.
    meta = _meta(600, 2, {"WebAttack": (200, 230)})
    support = split_blocks(meta, CFG, seed=3).support
    for split in SPLITS:
        assert support["WebAttack"].to_dict()[split] > 0


def test_purge_drops_block_starts_at_split_changes() -> None:
    meta = _meta(300, 4, {"DoS": (50, 120)})
    m = split_blocks(meta, CFG, seed=0).manifest.merge(meta, on=["capture_id", "source_row_index"])
    purged = m[m["split"] == PURGED]
    assert len(purged) > 0
    # Every purged row is in the first minute of its block.
    assert (purged["timestamp"].dt.minute % 5 == 0).all()
    # Rows on either side of every purge gap belong to different, non-purged splits only via a gap.
    kept = m[m["split"] != PURGED].sort_values("timestamp")
    changes = kept["split"].ne(kept["split"].shift()) & kept["split"].shift().notna()
    gaps = kept["timestamp"].diff()[changes]
    assert (gaps >= pd.Timedelta(minutes=2)).all()


def test_no_purge_when_disabled() -> None:
    meta = _meta(300, 2, {"DoS": (50, 120)})
    m = split_blocks(meta, SplitConfig(block_minutes=5, purge_minutes=0), seed=0).manifest
    assert PURGED not in set(m["split"])


def test_deterministic_and_seed_sensitive() -> None:
    meta = _meta(800, 2, {"DoS": (100, 300), "Bot": (500, 560)})
    a = split_blocks(meta, CFG, seed=7)
    b = split_blocks(meta.sample(frac=1.0, random_state=1), CFG, seed=7)  # row order irrelevant
    c = split_blocks(meta, CFG, seed=8)
    assert a.manifest_sha256 == b.manifest_sha256
    assert a.manifest_sha256 != c.manifest_sha256


def test_captures_never_share_blocks() -> None:
    meta = pd.concat([_meta(100, 2, {}, "mon"), _meta(100, 2, {"DDoS": (10, 60)}, "fri")])
    m = split_blocks(meta, CFG, seed=0).manifest
    assert m.groupby("block_id")["capture_id"].nunique().max() == 1
    assert all(b.startswith(f"{c}:") for b, c in zip(m["block_id"], m["capture_id"], strict=True))


def test_missing_columns_rejected() -> None:
    with pytest.raises(DataError) as info:
        split_blocks(pd.DataFrame({"capture_id": ["a"]}), CFG, seed=0)
    assert info.value.code is ErrorCode.SPLIT_INVALID


def test_empty_rejected() -> None:
    empty = pd.DataFrame(
        columns=["capture_id", "source_row_index", "timestamp", "label", "label_raw"]
    )
    with pytest.raises(DataError) as info:
        split_blocks(empty, CFG, seed=0)
    assert info.value.code is ErrorCode.DATA_EMPTY


def test_subtype_stratification_gives_every_subtype_training_rows() -> None:
    # Two DoS subtypes: a long dominant one and a short one. Stratifying on the merged class
    # can starve the short subtype of training rows; stratifying on label_raw must not.
    meta = _meta(900, 2, {"DoS": (100, 400)})
    short = (meta["timestamp"] >= pd.Timestamp("2017-07-05 17:00")) & (
        meta["timestamp"] < pd.Timestamp("2017-07-05 17:40")
    )
    meta.loc[short, "label"] = "DoS"
    meta["label_raw"] = meta["label"].str.lower()
    meta.loc[short, "label_raw"] = "dos slowloris"
    result = split_blocks(meta, CFG.model_copy(update={"stratify_by": "label_raw"}), seed=0)
    m = result.manifest.merge(meta, on=["capture_id", "source_row_index"])
    per_split = m[(m["label_raw"] == "dos slowloris") & (m["split"] != PURGED)][
        "split"
    ].value_counts()
    assert per_split.get("train", 0) > 0
    assert set(per_split.index) == set(SPLITS)
