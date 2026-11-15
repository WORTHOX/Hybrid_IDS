from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hnids.config import HnidsConfig, config_hash, load_config
from hnids.errors import ConfigError, ErrorCode
from tests.conftest import CONFIG_DIR, write_yaml

BASE = CONFIG_DIR / "base.yaml"
LOCAL = CONFIG_DIR / "local.yaml"


def _error_locs(exc: ConfigError) -> list[str]:
    errors = exc.details["errors"]
    assert isinstance(errors, list)
    return [e["loc"] for e in errors]


def test_profile_overrides_base(config: HnidsConfig) -> None:
    assert config.run.name == "hnids-local"
    assert config.run.log_json is False
    assert config.sequence.length == 20
    assert config.model.d_model == 256


def test_relative_paths_resolve_against_base_dir(tmp_path: Path) -> None:
    cfg = load_config([BASE, LOCAL], base_dir=tmp_path)
    assert cfg.paths.artifact_root == tmp_path.resolve() / "artifacts"
    assert cfg.paths.data_roots == (
        tmp_path.resolve() / "data" / "raw",
        tmp_path.resolve() / "models",
    )


def test_kaggle_profile_is_valid() -> None:
    cfg = load_config([BASE, CONFIG_DIR / "kaggle.yaml"])
    assert cfg.xgboost.device == "cuda"
    assert cfg.paths.artifact_root.name == "artifacts"


def test_config_is_immutable(config: HnidsConfig) -> None:
    with pytest.raises(ValidationError):
        config.run.seed = 7  # type: ignore[misc]


def test_unknown_key_is_rejected(tmp_path: Path, paths_override: dict[str, Any]) -> None:
    extra = write_yaml(tmp_path / "typo.yaml", {"training": {"epocs": 3}})
    with pytest.raises(ConfigError) as info:
        load_config([BASE, LOCAL, extra], overrides=paths_override)
    assert info.value.code is ErrorCode.CONFIG_INVALID
    assert "training.epocs" in _error_locs(info.value)


@pytest.mark.parametrize(
    ("override", "loc"),
    [
        ({"split": {"train_ratio": 0.8}}, "split"),
        ({"model": {"d_model": 250, "n_heads": 4}}, "model"),
        ({"sequence": {"length": 1}}, "sequence.length"),
        ({"run": {"seed": -1}}, "run.seed"),
        ({"serving": {"port": 70000}}, "serving.port"),
    ],
)
def test_invalid_values_fail_fast(
    override: dict[str, Any], loc: str, paths_override: dict[str, Any]
) -> None:
    with pytest.raises(ConfigError) as info:
        load_config([BASE, LOCAL], overrides={**paths_override, **override})
    assert loc in _error_locs(info.value)


def test_missing_file_has_specific_code(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_config([tmp_path / "nope.yaml"])
    assert info.value.code is ErrorCode.CONFIG_NOT_FOUND


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config([bad])


def test_malformed_yaml_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("run: {name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config([bad])


def test_yaml_tags_cannot_construct_objects(tmp_path: Path) -> None:
    evil = tmp_path / "evil.yaml"
    evil.write_text("run: !!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config([evil])


def test_no_files_rejected() -> None:
    with pytest.raises(ConfigError):
        load_config([])


def test_hash_is_stable_and_sensitive(paths_override: dict[str, Any], tmp_path: Path) -> None:
    a = load_config([BASE, LOCAL], overrides=paths_override)
    b = load_config([BASE, LOCAL], overrides=paths_override)
    c = load_config([BASE, LOCAL], overrides={**paths_override, "run": {"seed": 1}})
    assert config_hash(a) == config_hash(b)
    assert config_hash(a) != config_hash(c)


def test_hash_ignores_environment_paths(paths_override: dict[str, Any], tmp_path: Path) -> None:
    local = load_config([BASE, LOCAL], overrides=paths_override)
    elsewhere = load_config([BASE, LOCAL], base_dir=tmp_path)
    assert config_hash(local) == config_hash(elsewhere)
    assert config_hash(local, include_paths=True) != config_hash(elsewhere, include_paths=True)


def test_oof_groups_cannot_exceed_blocks(paths_override: dict[str, Any]) -> None:
    with pytest.raises(ConfigError) as info:
        load_config(
            [BASE, LOCAL],
            overrides={
                **paths_override,
                "xgboost": {"oof_group_minutes": 10},
                "split": {"block_minutes": 5},
            },
        )
    assert info.value.code is ErrorCode.CONFIG_INVALID
