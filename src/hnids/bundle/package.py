"""Versioned model bundles (ADR-0004).

Layout of ``bundles/<version>/``:
  bundle.json         metadata: classes, feature schema, architecture, temperature, thresholds
  scaler.json         train-only scaling parameters
  xgb.ubj             final XGBoost model (native format)
  model.safetensors   hybrid model weights
  eval_report.json    frozen test evaluation (+ eval_report.md)
  run_manifest.json   provenance
  manifest.json       SHA-256 of every file (verified before anything is deserialized)
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import torch
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from safetensors.torch import load_file, save_file

from hnids.errors import ArtifactIntegrityError, ErrorCode
from hnids.ingestion.schema import FEATURE_SCHEMA_VERSION
from hnids.ingestion.taxonomy import TAXONOMY_VERSION
from hnids.models.hybrid import ArchitectureSpec, HybridModel
from hnids.models.xgb_oof import XGBProbabilityModel
from hnids.preprocessing.scaler import FeatureScaler
from hnids.security.integrity import verify_manifest, write_manifest
from hnids.security.paths import AuthorizedRoots

BUNDLE_FORMAT_VERSION = 1
METADATA = "bundle.json"
SCALER = "scaler.json"
XGB_MODEL = "xgb.ubj"
WEIGHTS = "model.safetensors"
EVAL_JSON = "eval_report.json"
EVAL_MD = "eval_report.md"
RUN_MANIFEST = "run_manifest.json"
REQUIRED_FILES = (METADATA, SCALER, XGB_MODEL, WEIGHTS)


class BundleMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_format_version: int = BUNDLE_FORMAT_VERSION
    bundle_version: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    created_at: str
    class_names: tuple[str, ...]
    benign_class: str
    taxonomy_version: int
    feature_schema_version: int
    feature_names: tuple[str, ...]
    seq_len: int
    architecture: ArchitectureSpec
    temperature: float = Field(gt=0.0)
    alert_threshold: float = Field(ge=0.0, le=1.0)
    low_confidence_threshold: float = Field(ge=0.0, le=1.0)
    xgb_rounds: int
    config_hash: str
    summary_metrics: dict[str, float]


@dataclass(frozen=True)
class LoadedBundle:
    directory: Path
    metadata: BundleMetadata
    scaler: FeatureScaler
    xgb: XGBProbabilityModel
    model: HybridModel


def new_version(prefix: str = "b") -> str:
    return f"{prefix}{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def write_bundle(
    directory: Path,
    *,
    roots: AuthorizedRoots,
    metadata: BundleMetadata,
    scaler: FeatureScaler,
    xgb_model: XGBProbabilityModel,
    state_dict: dict[str, torch.Tensor],
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Write all bundle files, then seal them with an integrity manifest."""
    target = roots.resolve(directory, mode="write")
    if target.exists():
        raise ArtifactIntegrityError(
            f"bundle directory already exists: {target}", code=ErrorCode.ARTIFACT_UNEXPECTED_FILE
        )
    target.mkdir(parents=True)
    (target / METADATA).write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
    scaler.save(target / SCALER)
    xgb_model.save(target / XGB_MODEL)
    save_file(
        {k: v.detach().cpu().contiguous() for k, v in state_dict.items()}, str(target / WEIGHTS)
    )
    for name, content in (extra_files or {}).items():
        if "/" in name or "\\" in name or name.startswith("."):
            raise ArtifactIntegrityError(f"invalid bundle file name {name!r}")
        (target / name).write_text(content, encoding="utf-8")
    write_manifest(target)
    return target


def load_bundle(directory: Path, roots: AuthorizedRoots) -> LoadedBundle:
    """Verify integrity first, then load. Any problem raises ``ArtifactIntegrityError``."""
    source = roots.resolve(directory, mode="read", must_exist=True)
    verify_manifest(source)
    for name in REQUIRED_FILES:
        if not (source / name).is_file():
            raise ArtifactIntegrityError(
                f"bundle missing {name}", code=ErrorCode.ARTIFACT_MISSING_FILE
            )
    try:
        metadata = BundleMetadata.model_validate_json(
            (source / METADATA).read_text(encoding="utf-8")
        )
    except ValidationError as exc:
        raise ArtifactIntegrityError(
            "bundle metadata invalid", code=ErrorCode.ARTIFACT_INCOMPATIBLE
        ) from exc
    if metadata.bundle_format_version != BUNDLE_FORMAT_VERSION:
        raise ArtifactIntegrityError(
            "unsupported bundle format", code=ErrorCode.ARTIFACT_INCOMPATIBLE
        )
    if metadata.feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ArtifactIntegrityError(
            f"bundle feature schema v{metadata.feature_schema_version} "
            f"!= runtime v{FEATURE_SCHEMA_VERSION}",
            code=ErrorCode.ARTIFACT_INCOMPATIBLE,
        )
    if metadata.taxonomy_version != TAXONOMY_VERSION:
        raise ArtifactIntegrityError(
            "bundle taxonomy version mismatch", code=ErrorCode.ARTIFACT_INCOMPATIBLE
        )

    scaler = FeatureScaler.load(source / SCALER)
    if scaler.feature_names != metadata.feature_names:
        raise ArtifactIntegrityError(
            "scaler features differ from bundle metadata", code=ErrorCode.ARTIFACT_INCOMPATIBLE
        )
    xgb_model = XGBProbabilityModel.load(source / XGB_MODEL, n_classes=len(metadata.class_names))
    model = HybridModel(metadata.architecture)
    try:
        model.load_state_dict(load_file(str(source / WEIGHTS), device="cpu"), strict=True)
    except (RuntimeError, OSError) as exc:  # shape/key mismatch or corrupt tensor file
        raise ArtifactIntegrityError(
            "model weights incompatible", code=ErrorCode.ARTIFACT_INCOMPATIBLE
        ) from exc
    model.eval()
    return LoadedBundle(
        directory=source, metadata=metadata, scaler=scaler, xgb=xgb_model, model=model
    )


# ----------------------------------------------------------------------------- registry
ACTIVE_FILE = "ACTIVE.json"


class ActivePointer(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: str
    previous: str | None = None
    changed_at: str
    changed_by: str


def read_active(bundles_root: Path, roots: AuthorizedRoots) -> ActivePointer | None:
    path = roots.resolve(bundles_root / ACTIVE_FILE, mode="read")
    if not path.is_file():
        return None
    try:
        return ActivePointer.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ArtifactIntegrityError(
            "ACTIVE.json is malformed", code=ErrorCode.ARTIFACT_MANIFEST_INVALID
        ) from exc


def _write_active(bundles_root: Path, pointer: ActivePointer, roots: AuthorizedRoots) -> None:
    path = roots.resolve(bundles_root / ACTIVE_FILE, mode="write")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(pointer.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX and Windows


def activate(
    bundles_root: Path, version: str, roots: AuthorizedRoots, *, operator: str
) -> ActivePointer:
    """Verify then point ``ACTIVE.json`` at ``version``, remembering the previous bundle."""
    load_bundle(bundles_root / version, roots)  # refuses tampered/incompatible bundles
    current = read_active(bundles_root, roots)
    pointer = ActivePointer(
        active=version,
        previous=current.active
        if current and current.active != version
        else (current.previous if current else None),
        changed_at=datetime.now(UTC).isoformat(),
        changed_by=operator,
    )
    _write_active(bundles_root, pointer, roots)
    return pointer


def rollback(bundles_root: Path, roots: AuthorizedRoots, *, operator: str) -> ActivePointer:
    current = read_active(bundles_root, roots)
    if current is None or current.previous is None:
        raise ArtifactIntegrityError(
            "no previous bundle to roll back to", code=ErrorCode.MODEL_NOT_READY
        )
    load_bundle(bundles_root / current.previous, roots)
    pointer = ActivePointer(
        active=current.previous,
        previous=current.active,
        changed_at=datetime.now(UTC).isoformat(),
        changed_by=operator,
    )
    _write_active(bundles_root, pointer, roots)
    return pointer


def active_bundle_dir(bundles_root: Path, roots: AuthorizedRoots) -> Path:
    pointer = read_active(bundles_root, roots)
    if pointer is None:
        raise ArtifactIntegrityError("no active bundle", code=ErrorCode.MODEL_NOT_READY)
    return bundles_root / pointer.active


def import_bundle(source: Path, bundles_root: Path, roots: AuthorizedRoots) -> Path:
    """Copy a verified bundle (e.g. downloaded from Kaggle) into the local bundle registry."""
    loaded = load_bundle(source, roots)
    target = roots.resolve(bundles_root / loaded.metadata.bundle_version, mode="write")
    if target.exists():
        raise ArtifactIntegrityError(
            f"bundle {target.name} already imported", code=ErrorCode.ARTIFACT_UNEXPECTED_FILE
        )
    shutil.copytree(loaded.directory, target)
    verify_manifest(target)
    return target
