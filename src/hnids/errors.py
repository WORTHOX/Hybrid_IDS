"""Machine-readable domain errors.

Every failure the system reports to a caller carries a stable :class:`ErrorCode` so that
logs, API responses and tests can match on the code instead of the message text.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable error identifiers. Never rename a member; add new ones instead."""

    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_MISMATCH = "CONFIG_MISMATCH"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    PATH_OUTSIDE_ROOT = "PATH_OUTSIDE_ROOT"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    PATH_NOT_WRITABLE_ROOT = "PATH_NOT_WRITABLE_ROOT"
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    ARTIFACT_MISSING_FILE = "ARTIFACT_MISSING_FILE"
    ARTIFACT_UNEXPECTED_FILE = "ARTIFACT_UNEXPECTED_FILE"
    ARTIFACT_MANIFEST_INVALID = "ARTIFACT_MANIFEST_INVALID"
    ARTIFACT_INCOMPATIBLE = "ARTIFACT_INCOMPATIBLE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    DATA_EMPTY = "DATA_EMPTY"
    SPLIT_INVALID = "SPLIT_INVALID"
    INPUT_INVALID = "INPUT_INVALID"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    MODEL_NOT_READY = "MODEL_NOT_READY"


class HnidsError(Exception):
    """Base class for all domain errors."""

    default_code: ErrorCode = ErrorCode.INPUT_INVALID

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code if code is not None else self.default_code
        self.message = message
        self.details: dict[str, object] = dict(details or {})

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible mapping."""
        return {"code": self.code.value, "message": self.message, "details": self.details}

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


class ConfigError(HnidsError):
    default_code = ErrorCode.CONFIG_INVALID


class PathSecurityError(HnidsError):
    default_code = ErrorCode.PATH_OUTSIDE_ROOT


class ArtifactIntegrityError(HnidsError):
    default_code = ErrorCode.ARTIFACT_HASH_MISMATCH


class DataError(HnidsError):
    default_code = ErrorCode.SCHEMA_MISMATCH


class InputValidationError(HnidsError):
    default_code = ErrorCode.INPUT_INVALID
