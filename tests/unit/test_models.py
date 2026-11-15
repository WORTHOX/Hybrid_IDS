from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from hnids.config.schema import ModelConfig, XGBoostConfig
from hnids.errors import ArtifactIntegrityError, DataError, InputValidationError
from hnids.models.hybrid import ArchitectureSpec, HybridModel
from hnids.models.xgb_oof import (
    XGBProbabilityModel,
    balanced_sample_weights,
    check_probabilities,
    fit_oof,
    resolve_device,
)

pytestmark = pytest.mark.ml

XGB = XGBoostConfig(n_folds=3, n_estimators=15, max_depth=3, early_stopping_rounds=5, device="cpu")


def _data(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 3, n)
    x = rng.normal(0, 1, (n, 4)).astype(np.float32)
    x[:, 0] += y * 6.0
    groups = np.arange(n) // 20
    return x, y, groups


def test_oof_probabilities_are_valid_and_learn() -> None:
    x, y, g = _data(600, 0)
    vx, vy, _ = _data(150, 1)
    result = fit_oof(x, y, g, vx, vy, n_classes=3, config=XGB, seed=0)
    assert result.oof_train.shape == (600, 3)
    assert np.allclose(result.oof_train.sum(axis=1), 1, atol=1e-4)
    assert (result.oof_train.argmax(axis=1) == y).mean() > 0.9
    assert sum(result.fold_sizes) == 600
    assert 1 <= result.rounds <= XGB.n_estimators


def test_oof_rows_are_scored_by_models_that_never_saw_them() -> None:
    # Labels are pure noise keyed to row identity: a model that saw a row could memorize it;
    # OOF predictions of unseen rows must stay near chance.
    rng = np.random.default_rng(3)
    n = 900
    x = rng.normal(0, 1, (n, 6)).astype(np.float32)
    y = rng.integers(0, 3, n)
    groups = np.arange(n) // 30
    deep = XGB.model_copy(update={"max_depth": 10, "n_estimators": 60})
    result = fit_oof(x, y, groups, x[:0], y[:0], n_classes=3, config=deep, seed=1)
    in_sample = (result.final.predict_proba(x).argmax(axis=1) == y).mean()
    out_of_fold = (result.oof_train.argmax(axis=1) == y).mean()
    assert in_sample > 0.8
    assert out_of_fold < 0.45


def test_save_load_round_trip(tmp_path: Path) -> None:
    x, y, g = _data(300, 0)
    model = fit_oof(x, y, g, x[:50], y[:50], n_classes=3, config=XGB, seed=0).final
    path = tmp_path / "xgb.ubj"
    model.save(path)
    loaded = XGBProbabilityModel.load(path, n_classes=3)
    assert np.allclose(loaded.predict_proba(x[:10]), model.predict_proba(x[:10]), atol=1e-6)


def test_load_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "xgb.ubj"
    path.write_bytes(b"not a model")
    with pytest.raises(ArtifactIntegrityError):
        XGBProbabilityModel.load(path, n_classes=3)


def test_fit_requires_aligned_inputs_and_groups() -> None:
    x, y, g = _data(100, 0)
    with pytest.raises(DataError):
        fit_oof(x, y[:-1], g, x, y, n_classes=3, config=XGB, seed=0)
    with pytest.raises(DataError):
        fit_oof(x, y, np.zeros(100), x, y, n_classes=3, config=XGB, seed=0)


def test_check_probabilities_rejects_bad_rows() -> None:
    with pytest.raises(DataError):
        check_probabilities(np.array([[0.5, 0.6]]), 2)
    with pytest.raises(DataError):
        check_probabilities(np.array([[np.nan, 1.0]]), 2)
    with pytest.raises(DataError):
        check_probabilities(np.array([[1.0]]), 2)


def test_balanced_weights() -> None:
    w = balanced_sample_weights(np.array([0, 0, 0, 0, 1]), 3)
    assert w.mean() == pytest.approx(1.0)
    assert w[4] > w[0]


def test_resolve_device() -> None:
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in {"cpu", "cuda"}


SMALL = ModelConfig(
    gru_hidden=8, gru_layers=2, d_model=16, n_heads=4, ff_dim=32, transformer_layers=2, dropout=0.1
)


def test_hybrid_forward_shape_and_finite() -> None:
    spec = ArchitectureSpec(input_dim=11, n_classes=8, seq_len=20, model=SMALL)
    model = HybridModel(spec).eval()
    logits = model(torch.randn(4, 20, 11))
    assert logits.shape == (4, 8)
    assert torch.isfinite(logits).all()


def test_hybrid_default_architecture_from_prd() -> None:
    spec = ArchitectureSpec(input_dim=86, n_classes=8, seq_len=20, model=ModelConfig())
    model = HybridModel(spec)
    assert model.gru.hidden_size == 128 and model.gru.bidirectional and model.gru.num_layers == 2
    assert len(model.encoder.layers) == 2
    assert model.encoder.layers[0].self_attn.num_heads == 4
    assert model(torch.randn(2, 20, 86)).shape == (2, 8)


@pytest.mark.parametrize("shape", [(2, 19, 11), (2, 20, 10), (20, 11)])
def test_hybrid_rejects_wrong_shape(shape: tuple[int, ...]) -> None:
    model = HybridModel(ArchitectureSpec(input_dim=11, n_classes=8, seq_len=20, model=SMALL))
    with pytest.raises(InputValidationError):
        model(torch.randn(*shape))


def test_hybrid_is_order_sensitive() -> None:
    torch.manual_seed(0)
    model = HybridModel(ArchitectureSpec(input_dim=5, n_classes=3, seq_len=6, model=SMALL)).eval()
    x = torch.randn(1, 6, 5)
    assert not torch.allclose(model(x), model(x.flip(1)))


def test_require_gpu_fails_fast_without_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    from hnids.errors import ErrorCode, HnidsError
    from hnids.training.loop import select_device

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert select_device().type == "cpu"
    with pytest.raises(HnidsError) as info:
        select_device(require_gpu=True)
    assert info.value.code is ErrorCode.MODEL_NOT_READY
