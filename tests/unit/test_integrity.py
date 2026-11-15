from __future__ import annotations

import json
from pathlib import Path

import pytest

from hnids.errors import ArtifactIntegrityError, ErrorCode
from hnids.security import MANIFEST_NAME, sha256_file, verify_manifest, write_manifest


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "sub").mkdir(parents=True)
    (root / "weights.bin").write_bytes(b"\x00\x01\x02" * 100)
    (root / "sub" / "meta.json").write_text('{"a": 1}', encoding="utf-8")
    write_manifest(root)
    return root


def test_sha256_known_value(tmp_path: Path) -> None:
    f = tmp_path / "x"
    f.write_bytes(b"abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_round_trip_verifies(bundle: Path) -> None:
    manifest = verify_manifest(bundle)
    assert set(manifest.files) == {"weights.bin", "sub/meta.json"}


def test_modified_byte_detected(bundle: Path) -> None:
    data = bytearray((bundle / "weights.bin").read_bytes())
    data[10] ^= 0xFF
    (bundle / "weights.bin").write_bytes(bytes(data))
    with pytest.raises(ArtifactIntegrityError) as info:
        verify_manifest(bundle)
    assert info.value.code is ErrorCode.ARTIFACT_HASH_MISMATCH
    assert info.value.details["files"] == ["weights.bin"]


def test_deleted_file_detected(bundle: Path) -> None:
    (bundle / "sub" / "meta.json").unlink()
    with pytest.raises(ArtifactIntegrityError) as info:
        verify_manifest(bundle)
    assert info.value.code is ErrorCode.ARTIFACT_MISSING_FILE


def test_injected_file_detected(bundle: Path) -> None:
    (bundle / "evil.pkl").write_bytes(b"cos\nsystem\n")
    with pytest.raises(ArtifactIntegrityError) as info:
        verify_manifest(bundle)
    assert info.value.code is ErrorCode.ARTIFACT_UNEXPECTED_FILE


def test_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIntegrityError) as info:
        verify_manifest(tmp_path)
    assert info.value.code is ErrorCode.ARTIFACT_MANIFEST_INVALID


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"files": {}}),
        json.dumps({"created_at": "x", "files": {"a": {"sha256": "zz", "size": 1}}}),
    ],
)
def test_malformed_manifest(bundle: Path, content: str) -> None:
    (bundle / MANIFEST_NAME).write_text(content, encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError) as info:
        verify_manifest(bundle)
    assert info.value.code is ErrorCode.ARTIFACT_MANIFEST_INVALID


def test_unsupported_manifest_version(bundle: Path) -> None:
    raw = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
    raw["manifest_version"] = 99
    (bundle / MANIFEST_NAME).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError) as info:
        verify_manifest(bundle)
    assert info.value.code is ErrorCode.ARTIFACT_MANIFEST_INVALID
