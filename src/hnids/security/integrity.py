"""SHA-256 integrity manifests for artifact directories (FR-2).

A manifest lists every file in a directory (except the manifest itself) with its size and
digest. :func:`verify_manifest` fails on any modified, missing or unexpected file, so a
bundle is either exactly what was packaged or it is rejected.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hnids.errors import ArtifactIntegrityError, ErrorCode

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
_CHUNK = 1024 * 1024


class FileEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class ArtifactManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: int = MANIFEST_VERSION
    created_at: str
    files: dict[str, FileEntry]


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _scan(directory: Path) -> dict[str, FileEntry]:
    entries: dict[str, FileEntry] = {}
    for file in sorted(p for p in directory.rglob("*") if p.is_file()):
        rel = file.relative_to(directory).as_posix()
        if rel == MANIFEST_NAME:
            continue
        if file.is_symlink():
            raise ArtifactIntegrityError(
                f"symlinks are not allowed in artifacts: {rel}",
                code=ErrorCode.ARTIFACT_UNEXPECTED_FILE,
                details={"file": rel},
            )
        entries[rel] = FileEntry(sha256=sha256_file(file), size=file.stat().st_size)
    return entries


def write_manifest(directory: Path) -> ArtifactManifest:
    """Hash every file under ``directory`` and write ``manifest.json``."""
    manifest = ArtifactManifest(created_at=datetime.now(UTC).isoformat(), files=_scan(directory))
    (directory / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def read_manifest(directory: Path) -> ArtifactManifest:
    path = directory / MANIFEST_NAME
    if not path.is_file():
        raise ArtifactIntegrityError(
            f"manifest missing in {directory}",
            code=ErrorCode.ARTIFACT_MANIFEST_INVALID,
            details={"directory": str(directory)},
        )
    try:
        return ArtifactManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        raise ArtifactIntegrityError(
            f"manifest in {directory} is malformed",
            code=ErrorCode.ARTIFACT_MANIFEST_INVALID,
            details={"directory": str(directory)},
        ) from exc


def verify_manifest(directory: Path) -> ArtifactManifest:
    """Verify that ``directory`` matches its manifest exactly. Raises on any difference."""
    manifest = read_manifest(directory)
    if manifest.manifest_version != MANIFEST_VERSION:
        raise ArtifactIntegrityError(
            f"unsupported manifest version {manifest.manifest_version}",
            code=ErrorCode.ARTIFACT_MANIFEST_INVALID,
        )
    actual = _scan(directory)
    missing = sorted(set(manifest.files) - set(actual))
    if missing:
        raise ArtifactIntegrityError(
            f"artifact files missing: {missing}",
            code=ErrorCode.ARTIFACT_MISSING_FILE,
            details={"files": missing},
        )
    extra = sorted(set(actual) - set(manifest.files))
    if extra:
        raise ArtifactIntegrityError(
            f"unexpected artifact files: {extra}",
            code=ErrorCode.ARTIFACT_UNEXPECTED_FILE,
            details={"files": extra},
        )
    changed = sorted(name for name, entry in manifest.files.items() if actual[name] != entry)
    if changed:
        raise ArtifactIntegrityError(
            f"artifact files modified: {changed}",
            code=ErrorCode.ARTIFACT_HASH_MISMATCH,
            details={"files": changed},
        )
    return manifest
