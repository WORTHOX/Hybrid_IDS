from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hnids.config import HnidsConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs"


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    """Isolated data/artifact/quarantine roots for one test."""
    dirs = {
        "data": tmp_path / "data",
        "artifacts": tmp_path / "artifacts",
        "quarantine": tmp_path / "quarantine",
    }
    for d in dirs.values():
        d.mkdir()
    return dirs


@pytest.fixture
def paths_override(workspace: dict[str, Path]) -> dict[str, Any]:
    return {
        "paths": {
            "data_roots": [str(workspace["data"])],
            "artifact_root": str(workspace["artifacts"]),
            "quarantine_root": str(workspace["quarantine"]),
        }
    }


@pytest.fixture
def config(paths_override: dict[str, Any]) -> HnidsConfig:
    return load_config(
        [CONFIG_DIR / "base.yaml", CONFIG_DIR / "local.yaml"], overrides=paths_override
    )


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def tiny_bundle(tmp_path_factory: pytest.TempPathFactory) -> tuple[HnidsConfig, Path]:
    """Train once per session on synthetic data and return (config, bundle_dir)."""
    from hnids.security import AuthorizedRoots
    from hnids.workflows.train import run_training
    from tests.integration.test_training_pipeline import tiny_config, write_dataset

    root = tmp_path_factory.mktemp("bundle")
    cfg = tiny_config(root)
    write_dataset(root / "data")
    summary = run_training(cfg, AuthorizedRoots.from_config(cfg.paths), repo_dir=root)
    return cfg, Path(summary["bundle_dir"])
