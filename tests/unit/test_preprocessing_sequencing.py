from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from hnids.errors import ArtifactIntegrityError, DataError, ErrorCode
from hnids.preprocessing.scaler import FeatureScaler, signed_log1p
from hnids.sequencing.windows import build_window_index, gather_windows

NAMES = ("a", "b", "c")


def _train(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.column_stack(
        [rng.exponential(1000, 500), rng.normal(0, 1, 500), np.full(500, 7.0)]
    ).astype(np.float32)


def test_scaler_standardizes_training_data() -> None:
    train = _train()
    scaler = FeatureScaler.fit(train, NAMES)
    out = scaler.transform_array(train)
    assert out.dtype == np.float32
    assert np.allclose(out[:, :2].mean(axis=0), 0, atol=1e-4)
    assert np.allclose(out[:, :2].std(axis=0), 1, atol=1e-3)
    assert np.allclose(out[:, 2], 0, atol=1e-6)  # constant feature: scale falls back to 1


def test_scaler_fit_is_independent_of_eval_rows() -> None:
    train = _train()
    a = FeatureScaler.fit(train, NAMES)
    # Changing evaluation data cannot influence a scaler fitted on training rows only.
    _ = a.transform_array(_train(seed=99) * 1e6)
    b = FeatureScaler.fit(train, NAMES)
    assert a == b


def test_scaler_clips_extremes() -> None:
    scaler = FeatureScaler.fit(_train(), NAMES, clip=5.0)
    out = scaler.transform_array(np.array([[1e30, -1e30, 7.0]], dtype=np.float64))
    assert out.max() <= 5.0
    assert out.min() >= -5.0


def test_signed_log1p_is_odd() -> None:
    x = np.array([-100.0, -1.0, 0.0, 1.0, 100.0])
    assert np.allclose(signed_log1p(x), -signed_log1p(-x))


def test_scaler_json_round_trip(tmp_path: Path) -> None:
    scaler = FeatureScaler.fit(_train(), NAMES)
    path = tmp_path / "scaler.json"
    scaler.save(path)
    assert FeatureScaler.load(path) == scaler


def test_scaler_load_rejects_tampered_file(tmp_path: Path) -> None:
    path = tmp_path / "scaler.json"
    FeatureScaler.fit(_train(), NAMES).save(path)
    path.write_text(path.read_text().replace('"scale": [', '"scale": [-1.0, '), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError) as info:
        FeatureScaler.load(path)
    assert info.value.code is ErrorCode.ARTIFACT_INCOMPATIBLE


@pytest.mark.parametrize(
    "bad",
    [np.zeros((0, 3)), np.zeros((5, 2)), np.array([[np.inf, 0.0, 0.0]])],
)
def test_scaler_fit_rejects_bad_input(bad: np.ndarray) -> None:
    with pytest.raises(DataError):
        FeatureScaler.fit(bad, NAMES)


def test_transform_rejects_wrong_width() -> None:
    scaler = FeatureScaler.fit(_train(), NAMES)
    with pytest.raises(DataError) as info:
        scaler.transform_array(np.zeros((2, 4)))
    assert info.value.code is ErrorCode.SCHEMA_MISMATCH


def test_windows_never_cross_groups() -> None:
    groups = np.array([0] * 5 + [1] * 2 + [2] * 6)
    index = build_window_index(groups, length=3)
    assert index.ends.tolist() == [2, 3, 4, 9, 10, 11, 12]
    starts = index.ends - 2
    assert (groups[starts] == groups[index.ends]).all()
    s = index.stats
    assert (s.windows, s.groups, s.boundary_resets, s.short_groups) == (7, 3, 2, 1)
    assert s.warmup_rows == 2 + 2 + 2


def test_window_label_is_final_flow() -> None:
    labels = np.array([0, 0, 1, 1, 2])
    index = build_window_index(np.zeros(5, dtype=int), length=2)
    assert labels[index.ends].tolist() == [0, 1, 1, 2]


def test_non_contiguous_groups_rejected() -> None:
    with pytest.raises(DataError) as info:
        build_window_index(np.array([0, 0, 1, 1, 0, 0]), length=2)
    assert info.value.code is ErrorCode.SPLIT_INVALID


def test_invalid_length_and_empty() -> None:
    with pytest.raises(DataError):
        build_window_index(np.zeros(3), length=1)
    assert build_window_index(np.array([], dtype=int), length=3).stats.windows == 0


def test_gather_windows_shape_and_content() -> None:
    feats = np.arange(10 * 2, dtype=np.float32).reshape(10, 2)
    windows = gather_windows(feats, np.array([3, 9]), length=4)
    assert windows.shape == (2, 4, 2)
    assert windows[0, :, 0].tolist() == [0.0, 2.0, 4.0, 6.0]
    assert windows[1, -1].tolist() == feats[9].tolist()


def test_sub_block_groups_split_blocks_by_minute_and_never_merge_blocks() -> None:
    import pandas as pd

    from hnids.sequencing.windows import build_window_index
    from hnids.training.prepare import SplitArrays

    ts = pd.to_datetime(
        ["2017-07-07 14:50:05", "2017-07-07 14:50:40", "2017-07-07 14:51:10", "2017-07-07 14:56:00"]
    ).to_numpy()
    groups = np.array([0, 0, 0, 1])
    split = SplitArrays(
        name="train",
        features=np.zeros((4, 1), dtype=np.float32),
        labels=np.zeros(4, dtype=np.int64),
        groups=groups,
        capture_ids=np.array(["c"] * 4, dtype=object),
        timestamps=ts,
        flow_ids=np.array(["f"] * 4, dtype=object),
        windows=build_window_index(groups, 2),
    )
    codes = split.sub_block_groups(1)
    assert codes[0] == codes[1] != codes[2]
    assert len(set(codes)) == 3
    assert set(split.sub_block_groups(5)) == {0, 1}
