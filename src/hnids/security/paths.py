"""Authorized-root path containment (FR-2).

Every data, artifact and quarantine path is canonicalized (``..`` collapsed, symlinks
followed) and checked against an explicit allow-list of roots *before* any handle is opened.
Read access is granted to read roots and write roots; write access only to write roots.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hnids.config.schema import PathsConfig
from hnids.errors import ErrorCode, PathSecurityError

AccessMode = Literal["read", "write"]


def _canonical(path: Path, *, strict: bool) -> Path:
    try:
        return path.expanduser().resolve(strict=strict)
    except FileNotFoundError as exc:
        raise PathSecurityError(
            f"path does not exist: {path}",
            code=ErrorCode.PATH_NOT_FOUND,
            details={"path": str(path)},
        ) from exc
    except (OSError, RuntimeError) as exc:  # symlink loops, invalid names
        raise PathSecurityError(
            f"path cannot be resolved: {path}", details={"path": str(path), "reason": str(exc)}
        ) from exc


def _is_within(child: Path, root: Path) -> bool:
    child_s = os.path.normcase(str(child))
    root_s = os.path.normcase(str(root))
    try:
        return os.path.commonpath([child_s, root_s]) == root_s
    except ValueError:  # different drives on Windows
        return False


@dataclass(frozen=True)
class AuthorizedRoots:
    """Immutable allow-list of storage roots."""

    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]

    @classmethod
    def create(cls, read: Iterable[Path], write: Iterable[Path]) -> AuthorizedRoots:
        """Build from raw paths; roots are canonicalized non-strictly (they may not exist yet)."""
        write_roots = tuple(_canonical(Path(p), strict=False) for p in write)
        read_roots = tuple(_canonical(Path(p), strict=False) for p in read)
        if not write_roots and not read_roots:
            raise PathSecurityError("at least one authorized root is required")
        return cls(read_roots=read_roots, write_roots=write_roots)

    @classmethod
    def from_config(cls, paths: PathsConfig) -> AuthorizedRoots:
        write = [paths.artifact_root, paths.quarantine_root]
        if paths.scratch_root is not None:
            write.append(paths.scratch_root)
        return cls.create(read=paths.data_roots, write=write)

    def _allowed(self, mode: AccessMode) -> tuple[Path, ...]:
        return self.write_roots if mode == "write" else self.read_roots + self.write_roots

    def resolve(
        self, path: Path | str, *, mode: AccessMode = "read", must_exist: bool = False
    ) -> Path:
        """Return the canonical path if it lies inside an authorized root for ``mode``.

        Raises:
            PathSecurityError: ``PATH_OUTSIDE_ROOT`` for traversal or symlink escapes,
                ``PATH_NOT_WRITABLE_ROOT`` when writing under a read-only root,
                ``PATH_NOT_FOUND`` when ``must_exist`` and the path is absent.
        """
        resolved = _canonical(Path(path), strict=must_exist)
        if any(_is_within(resolved, root) for root in self._allowed(mode)):
            return resolved
        if mode == "write" and any(_is_within(resolved, root) for root in self.read_roots):
            raise PathSecurityError(
                f"write denied: {resolved} is under a read-only root",
                code=ErrorCode.PATH_NOT_WRITABLE_ROOT,
                details={"path": str(resolved)},
            )
        raise PathSecurityError(
            f"{mode} denied: {path} resolves outside authorized roots",
            details={"path": str(path), "resolved": str(resolved), "mode": mode},
        )

    def ensure_dir(self, path: Path | str) -> Path:
        """Resolve for write access and create the directory (and parents)."""
        resolved = self.resolve(path, mode="write")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
