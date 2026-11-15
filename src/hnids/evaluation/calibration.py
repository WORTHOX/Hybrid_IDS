"""Temperature scaling fitted on validation logits."""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

MIN_TEMPERATURE = 0.05
MAX_TEMPERATURE = 20.0


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scaled = logits.astype(np.float64) / temperature
    scaled -= scaled.max(axis=1, keepdims=True)
    exp = np.exp(scaled)
    probs: np.ndarray = exp / exp.sum(axis=1, keepdims=True)
    return probs


def fit_temperature(logits: np.ndarray, labels: np.ndarray, *, max_iter: int = 200) -> float:
    """Minimize validation NLL over a single scalar temperature. Returns 1.0 for empty input."""
    if len(labels) == 0:
        return 1.0
    x = torch.from_numpy(logits.astype(np.float64))
    y = torch.from_numpy(labels.astype(np.int64))
    log_t = torch.zeros(1, dtype=torch.float64, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_t], lr=0.1, max_iter=max_iter, line_search_fn="strong_wolfe")
    loss_fn = nn.CrossEntropyLoss()

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        loss: torch.Tensor = loss_fn(x / log_t.exp(), y)
        loss.backward()  # type: ignore[no-untyped-call]
        return loss

    optimizer.step(closure)  # type: ignore[no-untyped-call]
    temperature = float(log_t.exp().item())
    if not np.isfinite(temperature):
        return 1.0
    return float(np.clip(temperature, MIN_TEMPERATURE, MAX_TEMPERATURE))
