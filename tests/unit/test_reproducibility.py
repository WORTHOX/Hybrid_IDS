from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest
import torch

from hnids.config import HnidsConfig, config_hash
from hnids.errors import PathSecurityError
from hnids.reproducibility import build_run_manifest, seed_everything, write_run_manifest
from hnids.security import AuthorizedRoots


def _draw() -> tuple[float, float, float]:
    return random.random(), float(np.random.rand()), float(torch.rand(1).item())


def test_same_seed_same_draws() -> None:
    seed_everything(123)
    first = _draw()
    seed_everything(123)
    assert _draw() == first


def test_different_seed_different_draws() -> None:
    seed_everything(1)
    a = _draw()
    seed_everything(2)
    assert _draw() != a


def test_seeded_report_includes_torch() -> None:
    assert seed_everything(0, deterministic=False) == {"python": True, "numpy": True, "torch": True}


def test_run_manifest_fields(config: HnidsConfig, tmp_path: Path) -> None:
    manifest = build_run_manifest(config, stage="test", seeded={"python": True}, repo_dir=tmp_path)
    assert manifest.config_hash == config_hash(config)
    assert manifest.git.commit == "unknown"  # tmp_path is not a git repo
    assert manifest.lockfile_sha256 is None
    assert "numpy" in manifest.packages
    assert manifest.seed == config.run.seed


def test_run_manifest_written_inside_artifact_root(
    config: HnidsConfig, workspace: dict[str, Path], tmp_path: Path
) -> None:
    roots = AuthorizedRoots.from_config(config.paths)
    manifest = build_run_manifest(config, stage="test", seeded={}, repo_dir=tmp_path)
    path = write_run_manifest(manifest, workspace["artifacts"] / "runs" / "r1", roots)
    assert json.loads(path.read_text(encoding="utf-8"))["stage"] == "test"


def test_run_manifest_refuses_outside_root(config: HnidsConfig, tmp_path: Path) -> None:
    roots = AuthorizedRoots.from_config(config.paths)
    manifest = build_run_manifest(config, stage="test", seeded={}, repo_dir=tmp_path)
    with pytest.raises(PathSecurityError):
        write_run_manifest(manifest, tmp_path / "loose", roots)
