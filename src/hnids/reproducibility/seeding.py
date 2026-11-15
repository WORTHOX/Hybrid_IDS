"""Seed every source of randomness the pipeline uses (FR-1)."""

from __future__ import annotations

import importlib.util
import os
import random

import numpy as np


def seed_everything(seed: int, *, deterministic: bool = True) -> dict[str, bool]:
    """Seed Python, NumPy and (when installed) PyTorch.

    XGBoost is seeded per-model through its ``random_state`` parameter, which callers pass
    from the same configuration seed.

    Returns:
        Mapping of library name to whether it was seeded, for the run manifest.
    """
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)  # legacy global state still consumed by some libraries
    seeded = {"python": True, "numpy": True, "torch": False}

    if importlib.util.find_spec("torch") is not None:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        if deterministic:
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            torch.use_deterministic_algorithms(True, warn_only=True)
        seeded["torch"] = True
    return seeded
