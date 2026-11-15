"""Load and validate layered YAML configuration."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hnids.config.schema import HnidsConfig
from hnids.errors import ConfigError, ErrorCode

_PATH_KEYS = ("artifact_root", "quarantine_root", "scratch_root")


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path}",
            code=ErrorCode.CONFIG_NOT_FOUND,
            details={"path": str(path)},
        )
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}", details={"path": str(path)}) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"top level of {path} must be a mapping", details={"path": str(path)})
    return loaded


def _absolutize(value: str, base_dir: Path) -> str:
    expanded = Path(os.path.expandvars(value)).expanduser()
    return str(expanded if expanded.is_absolute() else (base_dir / expanded))


def _normalize_paths(raw: dict[str, Any], base_dir: Path) -> None:
    paths = raw.get("paths")
    if not isinstance(paths, dict):
        return
    roots = paths.get("data_roots")
    if isinstance(roots, list):
        paths["data_roots"] = [_absolutize(str(r), base_dir) for r in roots]
    for key in _PATH_KEYS:
        if isinstance(paths.get(key), str):
            paths[key] = _absolutize(paths[key], base_dir)


def load_config(
    files: Sequence[Path | str],
    *,
    overrides: Mapping[str, Any] | None = None,
    base_dir: Path | None = None,
) -> HnidsConfig:
    """Merge YAML files left-to-right, apply overrides, and validate.

    Relative paths in ``paths`` resolve against ``base_dir`` (default: current directory),
    and ``~``/``$VAR`` are expanded. Raises :class:`ConfigError` on any problem.
    """
    if not files:
        raise ConfigError("at least one config file is required")
    merged: dict[str, Any] = {}
    for file in files:
        merged = _deep_merge(merged, _read_yaml(Path(file)))
    if overrides:
        merged = _deep_merge(merged, overrides)
    _normalize_paths(merged, (base_dir or Path.cwd()).resolve())
    try:
        return HnidsConfig.model_validate(merged)
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in err["loc"]), "type": err["type"], "msg": err["msg"]}
            for err in exc.errors()
        ]
        raise ConfigError(
            f"configuration failed validation ({len(errors)} error(s))",
            details={"errors": errors},
        ) from exc


def config_to_dict(config: HnidsConfig) -> dict[str, Any]:
    """JSON-compatible representation (paths as POSIX strings)."""
    data: dict[str, Any] = config.model_dump(mode="json")
    return data


def config_hash(config: HnidsConfig, *, include_paths: bool = False) -> str:
    """Stable SHA-256 over the canonical JSON form of the configuration.

    Storage roots are environment-specific (local vs. Kaggle), so they are excluded by
    default: two runs with identical behavior-relevant settings hash identically.
    """
    data = config_to_dict(config)
    if not include_paths:
        data.pop("paths", None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
