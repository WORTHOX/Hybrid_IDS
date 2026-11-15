"""Run provenance record (FR-1): what code, config, environment and inputs produced a result."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import shutil
import subprocess  # nosec B404
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from hnids import __version__
from hnids.config.loader import config_hash, config_to_dict
from hnids.config.schema import HnidsConfig
from hnids.security.integrity import sha256_file
from hnids.security.paths import AuthorizedRoots

TRACKED_PACKAGES = (
    "numpy",
    "pandas",
    "pyarrow",
    "pydantic",
    "scikit-learn",
    "torch",
    "xgboost",
    "safetensors",
    "fastapi",
)


class GitInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    commit: str
    dirty: bool | None


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    created_at: str
    hnids_version: str
    stage: str
    git: GitInfo
    config_hash: str
    config: dict[str, Any]
    seed: int
    seeded: dict[str, bool]
    python: str
    platform: str
    packages: dict[str, str]
    lockfile_sha256: str | None
    accelerator: dict[str, Any]
    inputs: dict[str, str]


def _git_info(repo_dir: Path) -> GitInfo:
    git = shutil.which("git")
    if git is None or not (repo_dir / ".git").exists():
        return GitInfo(commit="unknown", dirty=None)
    try:
        commit = subprocess.run(  # noqa: S603  # nosec B603
            [git, "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603  # nosec B603
            [git, "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return GitInfo(commit="unknown", dirty=None)
    return GitInfo(commit=commit, dirty=bool(status.strip()))


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _accelerator() -> dict[str, Any]:
    try:
        import torch
    except ImportError:
        return {"torch": False}
    info: dict[str, Any] = {"torch": True, "cuda_available": torch.cuda.is_available()}
    if torch.cuda.is_available():
        info["device_count"] = torch.cuda.device_count()
        info["devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        info["cuda_version"] = torch.version.cuda
    return info


def build_run_manifest(
    config: HnidsConfig,
    *,
    stage: str,
    seeded: dict[str, bool],
    repo_dir: Path | None = None,
    inputs: dict[str, str] | None = None,
) -> RunManifest:
    repo = (repo_dir or Path.cwd()).resolve()
    lock = repo / "uv.lock"
    return RunManifest(
        run_id=uuid.uuid4().hex,
        created_at=datetime.now(UTC).isoformat(),
        hnids_version=__version__,
        stage=stage,
        git=_git_info(repo),
        config_hash=config_hash(config),
        config=config_to_dict(config),
        seed=config.run.seed,
        seeded=seeded,
        python=sys.version.split()[0],
        platform=platform.platform(),
        packages=_package_versions(),
        lockfile_sha256=sha256_file(lock) if lock.is_file() else None,
        accelerator=_accelerator(),
        inputs=dict(inputs or {}),
    )


def write_run_manifest(manifest: RunManifest, directory: Path, roots: AuthorizedRoots) -> Path:
    """Write ``run_manifest.json`` into ``directory``, which must be under a write root."""
    target_dir = roots.ensure_dir(directory)
    target = roots.resolve(target_dir / "run_manifest.json", mode="write")
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
    )
    return target
