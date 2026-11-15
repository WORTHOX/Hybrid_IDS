"""Hybrid model training (FR-8).

Class-weighted cross-entropy, AdamW, ReduceLROnPlateau on validation macro-F1, mixed precision
on CUDA, gradient clipping, early stopping, and safetensors checkpoints after every epoch.
The fused feature matrix stays on the device and windows are gathered per batch by index.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import save_file
from sklearn.metrics import f1_score
from torch import nn

from hnids.config.schema import TrainingConfig
from hnids.errors import ErrorCode, HnidsError
from hnids.logging import get_logger
from hnids.models.hybrid import ArchitectureSpec, HybridModel
from hnids.security.paths import AuthorizedRoots

_log = get_logger("training.loop")


@dataclass(frozen=True)
class WindowSet:
    fused: np.ndarray  # (N, F+C) float32 rows, block-contiguous
    labels: np.ndarray  # (N,) int64 per-row labels
    ends: np.ndarray  # (W,) window end indices

    @property
    def window_labels(self) -> np.ndarray:
        selected: np.ndarray = self.labels[self.ends]
        return selected


@dataclass
class TrainResult:
    state_dict: dict[str, torch.Tensor]
    best_epoch: int
    best_val_macro_f1: float
    history: list[dict[str, float]] = field(default_factory=list)


def select_device(*, require_gpu: bool = False) -> torch.device:
    """CUDA when available. With ``require_gpu`` a missing GPU is a hard error, never a
    silent CPU fallback."""
    if torch.cuda.is_available():
        return torch.device("cuda")
    if require_gpu:
        raise HnidsError(
            "GPU required by configuration but CUDA is not available",
            code=ErrorCode.MODEL_NOT_READY,
        )
    return torch.device("cpu")


def class_weights(labels: np.ndarray, n_classes: int) -> torch.Tensor:
    """Inverse square-root frequency; absent classes get weight 0. Normalized to mean 1."""
    counts = np.bincount(labels, minlength=n_classes).astype(np.float64)
    weights = np.where(counts > 0, 1.0 / np.sqrt(np.maximum(counts, 1.0)), 0.0)
    present = weights > 0
    weights[present] = weights[present] / weights[present].mean()
    return torch.tensor(weights, dtype=torch.float32)


def _gather(rows: torch.Tensor, ends: torch.Tensor, seq_len: int) -> torch.Tensor:
    offsets = torch.arange(-seq_len + 1, 1, device=ends.device)
    return rows[ends.unsqueeze(1) + offsets.unsqueeze(0)]


@torch.no_grad()
def predict_logits(
    model: HybridModel,
    fused: np.ndarray,
    ends: np.ndarray,
    *,
    batch_size: int = 2048,
    device: torch.device | None = None,
) -> np.ndarray:
    """Logits for every window (eval mode, no AMP for numerical stability)."""
    device = device or next(model.parameters()).device
    model.eval()
    rows = torch.from_numpy(np.ascontiguousarray(fused)).to(device)
    end_t = torch.from_numpy(ends.astype(np.int64)).to(device)
    outputs = []
    for start in range(0, len(end_t), batch_size):
        batch = _gather(rows, end_t[start : start + batch_size], model.spec.seq_len)
        outputs.append(model(batch).float().cpu())
    if not outputs:
        return np.empty((0, model.spec.n_classes), dtype=np.float32)
    return torch.cat(outputs).numpy()


def _save_checkpoint(
    model: HybridModel, directory: Path, name: str, meta: dict[str, float], roots: AuthorizedRoots
) -> None:
    weights = roots.resolve(directory / f"{name}.safetensors", mode="write")
    state = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    save_file(state, str(weights))
    roots.resolve(directory / f"{name}.json", mode="write").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def train_hybrid(
    train: WindowSet,
    val: WindowSet,
    *,
    spec: ArchitectureSpec,
    config: TrainingConfig,
    checkpoint_dir: Path,
    roots: AuthorizedRoots,
    seed: int,
    device: torch.device | None = None,
) -> TrainResult:
    device = device or select_device()
    checkpoint_dir = roots.ensure_dir(checkpoint_dir)
    generator = np.random.default_rng(seed)
    model = HybridModel(spec).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=config.lr_reduce_factor, patience=config.lr_reduce_patience
    )
    loss_fn = nn.CrossEntropyLoss(
        weight=class_weights(train.window_labels, spec.n_classes).to(device)
    )
    use_amp = config.amp and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    rows = torch.from_numpy(np.ascontiguousarray(train.fused)).to(device)
    labels = torch.from_numpy(np.array(train.labels, dtype=np.int64, copy=True)).to(device)
    val_truth = val.window_labels

    best_f1 = -1.0
    best_epoch = -1
    best_state: dict[str, torch.Tensor] = {}
    stale = 0
    history: list[dict[str, float]] = []
    for epoch in range(config.epochs):
        started = time.perf_counter()
        model.train()
        ends = train.ends
        if config.max_train_windows and len(ends) > config.max_train_windows:
            ends = generator.choice(ends, size=config.max_train_windows, replace=False)
        order = torch.from_numpy(generator.permutation(ends)).to(device)
        total_loss = 0.0
        for start in range(0, len(order), config.batch_size):
            batch_ends = order[start : start + config.batch_size]
            x = _gather(rows, batch_ends, spec.seq_len)
            y = labels[batch_ends]
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
                loss = loss_fn(model(x), y)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip_norm)
            scaler.step(optimizer)
            scaler.update()
            total_loss += float(loss.detach()) * len(batch_ends)

        val_pred = predict_logits(model, val.fused, val.ends, device=device).argmax(axis=1)
        val_f1 = (
            float(
                f1_score(
                    val_truth,
                    val_pred,
                    average="macro",
                    labels=np.unique(val_truth),
                    zero_division=0,
                )
            )
            if len(val_truth)
            else 0.0
        )
        scheduler.step(val_f1)
        record = {
            "epoch": float(epoch),
            "train_loss": total_loss / max(len(order), 1),
            "val_macro_f1": val_f1,
            "lr": float(optimizer.param_groups[0]["lr"]),
            "seconds": time.perf_counter() - started,
        }
        history.append(record)
        _log.info("epoch_done", **record)
        _save_checkpoint(model, checkpoint_dir, "last", record, roots)
        if val_f1 > best_f1:
            best_f1, best_epoch, stale = val_f1, epoch, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            _save_checkpoint(model, checkpoint_dir, "best", record, roots)
        else:
            stale += 1
            if stale >= config.early_stopping_patience:
                _log.info("early_stopping", epoch=epoch, best_epoch=best_epoch)
                break
    return TrainResult(
        state_dict=best_state, best_epoch=best_epoch, best_val_macro_f1=best_f1, history=history
    )
