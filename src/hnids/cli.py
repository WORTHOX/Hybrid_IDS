"""``hnids`` command-line entry point.

Commands write a single JSON document to stdout and structured logs to stderr. Domain errors
exit with status 2 and print the error's machine-readable form.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from hnids.config import config_hash, load_config
from hnids.config.schema import HnidsConfig
from hnids.errors import HnidsError
from hnids.logging import configure_logging, get_logger
from hnids.reproducibility import build_run_manifest, seed_everything, write_run_manifest
from hnids.security import AuthorizedRoots

EXIT_OK = 0
EXIT_DOMAIN_ERROR = 2

Handler = Callable[[argparse.Namespace], dict[str, Any]]


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _load(args: argparse.Namespace) -> HnidsConfig:
    config = load_config(args.config, base_dir=args.base_dir)
    configure_logging(config.run.log_level, json=config.run.log_json)
    return config


def _cmd_validate_config(args: argparse.Namespace) -> dict[str, Any]:
    config = _load(args)
    return {"valid": True, "config_hash": config_hash(config), "run": config.run.name}


def _cmd_env_report(args: argparse.Namespace) -> dict[str, Any]:
    config = _load(args)
    log = get_logger("cli.env_report")
    roots = AuthorizedRoots.from_config(config.paths)
    seeded = seed_everything(config.run.seed, deterministic=config.run.deterministic)
    data_roots = {str(r): r.is_dir() for r in roots.read_roots}
    manifest = build_run_manifest(config, stage="env-report", seeded=seeded)
    out_dir = config.paths.artifact_root / "runs" / manifest.run_id
    path = write_run_manifest(manifest, out_dir, roots)
    log.info("env_report_written", path=str(path), data_roots=data_roots)
    return {
        "run_id": manifest.run_id,
        "manifest_path": str(path),
        "data_roots_exist": data_roots,
        "accelerator": manifest.accelerator,
        "packages": manifest.packages,
    }


def _cmd_train(args: argparse.Namespace) -> dict[str, Any]:
    from hnids.workflows.train import run_training

    config = _load(args)
    return run_training(config, AuthorizedRoots.from_config(config.paths))


def _bundles_root(config: HnidsConfig) -> Path:
    return config.paths.artifact_root / "bundles"


def _cmd_bundle_verify(args: argparse.Namespace) -> dict[str, Any]:
    from hnids.bundle.package import load_bundle

    config = _load(args)
    loaded = load_bundle(args.path, AuthorizedRoots.from_config(config.paths))
    return {
        "verified": True,
        "bundle_version": loaded.metadata.bundle_version,
        "metrics": loaded.metadata.summary_metrics,
    }


def _cmd_bundle_import(args: argparse.Namespace) -> dict[str, Any]:
    from hnids.bundle.package import import_bundle

    config = _load(args)
    target = import_bundle(
        args.path, _bundles_root(config), AuthorizedRoots.from_config(config.paths)
    )
    return {"imported": str(target)}


def _cmd_bundle_activate(args: argparse.Namespace) -> dict[str, Any]:
    from hnids.bundle.package import activate

    config = _load(args)
    pointer = activate(
        _bundles_root(config),
        args.version,
        AuthorizedRoots.from_config(config.paths),
        operator=getpass.getuser(),
    )
    get_logger("cli.bundle").info("bundle_activated", **pointer.model_dump())
    return pointer.model_dump()


def _cmd_bundle_rollback(args: argparse.Namespace) -> dict[str, Any]:
    from hnids.bundle.package import rollback

    config = _load(args)
    pointer = rollback(
        _bundles_root(config), AuthorizedRoots.from_config(config.paths), operator=getpass.getuser()
    )
    get_logger("cli.bundle").info("bundle_rolled_back", **pointer.model_dump())
    return pointer.model_dump()


def _cmd_serve(args: argparse.Namespace) -> dict[str, Any]:  # pragma: no cover - blocks
    import uvicorn

    from hnids.bundle.package import active_bundle_dir
    from hnids.serving.app import create_app

    config = _load(args)
    roots = AuthorizedRoots.from_config(config.paths)
    bundle = args.bundle or active_bundle_dir(_bundles_root(config), roots)
    app = create_app(bundle_dir=bundle, roots=roots, serving=config.serving)
    uvicorn.run(app, host=config.serving.host, port=config.serving.port, log_config=None)
    return {"stopped": True}


def _cmd_release_report(args: argparse.Namespace) -> dict[str, Any]:
    from hnids.bundle.package import active_bundle_dir
    from hnids.workflows.release import write_release_report

    config = _load(args)
    config_roots = AuthorizedRoots.from_config(config.paths)
    bundle = (
        _bundles_root(config) / args.version
        if args.version
        else active_bundle_dir(_bundles_root(config), config_roots)
    )
    # The operator-named evidence directory is the only extra write root.
    roots = AuthorizedRoots.create(
        read=config_roots.read_roots, write=(*config_roots.write_roots, args.evidence_dir)
    )
    path = write_release_report(bundle, args.evidence_dir, roots)
    return {"report": str(path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hnids", description="Hybrid NIDS toolkit")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="directory that relative config paths resolve against (default: cwd)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Handler, help_text: str) -> argparse.ArgumentParser:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument(
            "--config",
            "-c",
            nargs="+",
            required=True,
            type=Path,
            help="YAML files, merged in order",
        )
        cmd.set_defaults(handler=handler)
        return cmd

    add("validate-config", _cmd_validate_config, "validate configuration and print its hash")
    add("env-report", _cmd_env_report, "check roots/accelerator and write a run manifest")
    add("train", _cmd_train, "ingest, train, evaluate and package a model bundle")
    add("bundle-verify", _cmd_bundle_verify, "verify and load a bundle").add_argument(
        "--path", type=Path, required=True
    )
    add(
        "bundle-import", _cmd_bundle_import, "copy a verified bundle into the registry"
    ).add_argument("--path", type=Path, required=True)
    add("bundle-activate", _cmd_bundle_activate, "make a registry bundle active").add_argument(
        "--version", required=True
    )
    add("bundle-rollback", _cmd_bundle_rollback, "re-activate the previous bundle")
    report = add("release-report", _cmd_release_report, "write the release evidence report")
    report.add_argument("--version", default=None, help="bundle version (default: active)")
    report.add_argument("--evidence-dir", type=Path, default=Path("release"))
    add("serve", _cmd_serve, "run the FastAPI detection service").add_argument(
        "--bundle", type=Path, default=None, help="bundle directory (default: ACTIVE.json)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except HnidsError as exc:
        _emit({"ok": False, "error": exc.to_dict()})
        return EXIT_DOMAIN_ERROR
    _emit({"ok": True, **payload})
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
