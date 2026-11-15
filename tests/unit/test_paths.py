from __future__ import annotations

import os
from pathlib import Path

import pytest

from hnids.errors import ErrorCode, PathSecurityError
from hnids.security import AuthorizedRoots


@pytest.fixture
def roots(workspace: dict[str, Path]) -> AuthorizedRoots:
    return AuthorizedRoots.create(
        read=[workspace["data"]], write=[workspace["artifacts"], workspace["quarantine"]]
    )


def _can_symlink(tmp_path: Path) -> bool:
    probe = tmp_path / "probe-link"
    try:
        probe.symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def test_read_inside_data_root(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    file = workspace["data"] / "a.csv"
    file.write_text("x", encoding="utf-8")
    assert roots.resolve(file, must_exist=True) == file.resolve()


def test_write_roots_are_readable(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    target = workspace["artifacts"] / "model" / "w.bin"
    assert roots.resolve(target, mode="read") == target.resolve()


def test_dotdot_traversal_rejected(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    sneaky = workspace["data"] / ".." / ".." / "secrets.txt"
    with pytest.raises(PathSecurityError) as info:
        roots.resolve(sneaky)
    assert info.value.code is ErrorCode.PATH_OUTSIDE_ROOT


def test_sibling_prefix_is_not_inside(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    # "/tmp/x/data-evil" shares a string prefix with "/tmp/x/data" but is not inside it.
    evil = workspace["data"].parent / "data-evil" / "f.csv"
    with pytest.raises(PathSecurityError):
        roots.resolve(evil)


def test_absolute_outside_rejected(roots: AuthorizedRoots, tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError):
        roots.resolve(tmp_path.parent / "elsewhere.txt")


def test_write_to_read_only_root_rejected(
    roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    with pytest.raises(PathSecurityError) as info:
        roots.resolve(workspace["data"] / "out.parquet", mode="write")
    assert info.value.code is ErrorCode.PATH_NOT_WRITABLE_ROOT


def test_write_outside_everything_rejected(roots: AuthorizedRoots, tmp_path: Path) -> None:
    with pytest.raises(PathSecurityError) as info:
        roots.resolve(tmp_path / "loose.txt", mode="write")
    assert info.value.code is ErrorCode.PATH_OUTSIDE_ROOT


def test_must_exist(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    with pytest.raises(PathSecurityError) as info:
        roots.resolve(workspace["data"] / "missing.csv", must_exist=True)
    assert info.value.code is ErrorCode.PATH_NOT_FOUND


def test_symlink_escape_rejected(
    roots: AuthorizedRoots, workspace: dict[str, Path], tmp_path: Path
) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("symlink creation not permitted on this host")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    link = workspace["data"] / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSecurityError) as info:
        roots.resolve(link / "secret.txt", must_exist=True)
    assert info.value.code is ErrorCode.PATH_OUTSIDE_ROOT


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive filesystem semantics")
def test_case_variation_on_windows(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    upper = Path(str(workspace["data"]).upper()) / "x.csv"
    assert roots.resolve(upper) is not None


def test_ensure_dir_creates_under_write_root(
    roots: AuthorizedRoots, workspace: dict[str, Path]
) -> None:
    created = roots.ensure_dir(workspace["artifacts"] / "runs" / "abc")
    assert created.is_dir()


def test_ensure_dir_refuses_read_root(roots: AuthorizedRoots, workspace: dict[str, Path]) -> None:
    with pytest.raises(PathSecurityError):
        roots.ensure_dir(workspace["data"] / "new")
    assert not (workspace["data"] / "new").exists()


def test_empty_roots_rejected() -> None:
    with pytest.raises(PathSecurityError):
        AuthorizedRoots.create(read=[], write=[])


def test_from_config(config: object, workspace: dict[str, Path]) -> None:
    from hnids.config import HnidsConfig

    assert isinstance(config, HnidsConfig)
    roots = AuthorizedRoots.from_config(config.paths)
    assert workspace["data"].resolve() in roots.read_roots
    assert workspace["artifacts"].resolve() in roots.write_roots
