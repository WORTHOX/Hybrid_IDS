from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hnids.cli import EXIT_DOMAIN_ERROR, EXIT_OK, main
from tests.conftest import CONFIG_DIR, write_yaml

pytestmark = pytest.mark.integration


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _profile(tmp_path: Path, workspace: dict[str, Path]) -> Path:
    return write_yaml(
        tmp_path / "profile.yaml",
        {
            "run": {"name": "cli-test", "log_json": True},
            "paths": {
                "data_roots": [str(workspace["data"])],
                "artifact_root": str(workspace["artifacts"]),
                "quarantine_root": str(workspace["quarantine"]),
            },
        },
    )


def test_validate_config_ok(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, workspace: dict[str, Path]
) -> None:
    code, out = _run(
        capsys,
        "validate-config",
        "-c",
        str(CONFIG_DIR / "base.yaml"),
        str(_profile(tmp_path, workspace)),
    )
    assert code == EXIT_OK
    assert out["ok"] is True
    assert len(out["config_hash"]) == 64


def test_validate_config_bad_key_exits_with_code(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, workspace: dict[str, Path]
) -> None:
    bad = write_yaml(tmp_path / "bad.yaml", {"model": {"layers": 3}})
    code, out = _run(
        capsys,
        "validate-config",
        "-c",
        str(CONFIG_DIR / "base.yaml"),
        str(_profile(tmp_path, workspace)),
        str(bad),
    )
    assert code == EXIT_DOMAIN_ERROR
    assert out["error"]["code"] == "CONFIG_INVALID"


def test_env_report_writes_manifest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, workspace: dict[str, Path]
) -> None:
    code, out = _run(
        capsys,
        "env-report",
        "-c",
        str(CONFIG_DIR / "base.yaml"),
        str(_profile(tmp_path, workspace)),
    )
    assert code == EXIT_OK
    manifest = Path(out["manifest_path"])
    assert manifest.is_file()
    assert manifest.resolve().is_relative_to(workspace["artifacts"].resolve())
    assert out["data_roots_exist"] == {str(workspace["data"].resolve()): True}
