"""Path containment and artifact integrity."""

from hnids.security.integrity import (
    MANIFEST_NAME,
    ArtifactManifest,
    read_manifest,
    sha256_file,
    verify_manifest,
    write_manifest,
)
from hnids.security.paths import AuthorizedRoots

__all__ = [
    "MANIFEST_NAME",
    "ArtifactManifest",
    "AuthorizedRoots",
    "read_manifest",
    "sha256_file",
    "verify_manifest",
    "write_manifest",
]
