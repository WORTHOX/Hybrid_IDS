"""Drive training on Kaggle GPUs through the official Kaggle CLI (ADR-0005).

    python scripts/kaggle_run.py push  [--extra smoke.yaml]   # build, upload bundle, start kernel
    python scripts/kaggle_run.py status                      # print kernel status
    python scripts/kaggle_run.py pull                        # download outputs + verify bundles

Requires `kaggle auth login` (OAuth). Credentials stay in ~/.kaggle; nothing secret is
written to the bundle or repo.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist"
BUNDLE_STAGE = DIST / "kaggle-bundle"
KERNEL_STAGE = DIST / "kaggle-kernel"
DATASET = "pshikk/cicids2017-untampered"
BUNDLE_SLUG = "hnids-bundle"
KERNEL_SLUG = "hnids-train"
ACCELERATOR = "NvidiaTeslaT4"


def kaggle_bin() -> str:
    found = shutil.which("kaggle") or str(
        Path.home() / ".local" / "bin" / ("kaggle.exe" if os.name == "nt" else "kaggle")
    )
    return found


def username() -> str:
    """Active Kaggle account (works for OAuth, access-token and legacy auth)."""
    out = subprocess.run(
        [kaggle_bin(), "config", "view"], capture_output=True, text=True, check=True
    )
    for line in out.stdout.splitlines():
        if line.strip().startswith("- username:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("not logged in to Kaggle: run `kaggle auth login`")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.returncode != 0:
        print(result.stderr.rstrip(), file=sys.stderr, flush=True)
        if check:
            raise SystemExit(result.returncode)
    return result


def stage_bundle(user: str) -> None:
    shutil.rmtree(BUNDLE_STAGE, ignore_errors=True)
    for old in DIST.glob("hnids-*.whl"):
        old.unlink()
    uv = shutil.which("uv") or "uv"
    run([uv, "build", "--wheel", "--out-dir", str(DIST)])
    BUNDLE_STAGE.mkdir(parents=True)
    wheel = next(DIST.glob("hnids-*.whl"))
    shutil.copy2(wheel, BUNDLE_STAGE / wheel.name)
    for cfg in (REPO / "configs").glob("*.yaml"):  # flat: Kaggle archives subdirectories
        shutil.copy2(cfg, BUNDLE_STAGE / cfg.name)
    meta = {"title": BUNDLE_SLUG, "id": f"{user}/{BUNDLE_SLUG}", "licenses": [{"name": "other"}]}
    (BUNDLE_STAGE / "dataset-metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def upload_bundle(user: str) -> None:
    exists = run([kaggle_bin(), "datasets", "status", f"{user}/{BUNDLE_SLUG}"], check=False)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if exists.returncode == 0 and "ready" in exists.stdout.lower():
        run([kaggle_bin(), "datasets", "version", "-p", str(BUNDLE_STAGE), "-m", f"hnids {stamp}"])
    else:
        run([kaggle_bin(), "datasets", "create", "-p", str(BUNDLE_STAGE)])  # private by default
    for _ in range(60):
        status = run([kaggle_bin(), "datasets", "status", f"{user}/{BUNDLE_SLUG}"], check=False)
        if "ready" in status.stdout.lower():
            return
        time.sleep(10)
    raise SystemExit("bundle dataset did not become ready")


def stage_kernel(user: str, extra: list[str]) -> None:
    shutil.rmtree(KERNEL_STAGE, ignore_errors=True)
    KERNEL_STAGE.mkdir(parents=True)
    source = (REPO / "kaggle" / "entry.py").read_text(encoding="utf-8")
    source = source.replace(
        "EXTRA_CONFIGS: list[str] = []", f"EXTRA_CONFIGS: list[str] = {extra!r}"
    )
    source = source.replace("__BUNDLE_SLUG__", BUNDLE_SLUG)
    (KERNEL_STAGE / "entry.py").write_text(source, encoding="utf-8")
    meta = {
        "id": f"{user}/{KERNEL_SLUG}",
        "title": KERNEL_SLUG,
        "code_file": "entry.py",
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": True,
        "enable_tpu": False,
        "enable_internet": True,
        "machine_shape": ACCELERATOR,
        "dataset_sources": [DATASET, f"{user}/{BUNDLE_SLUG}"],
        "competition_sources": [],
        "kernel_sources": [],
        "model_sources": [],
    }
    (KERNEL_STAGE / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def cmd_push(args: argparse.Namespace) -> None:
    user = username()
    stage_bundle(user)
    upload_bundle(user)
    stage_kernel(user, args.extra)
    run([kaggle_bin(), "kernels", "push", "-p", str(KERNEL_STAGE), "--accelerator", ACCELERATOR])
    print(f"kernel: https://www.kaggle.com/code/{user}/{KERNEL_SLUG}")


def cmd_status(_: argparse.Namespace) -> None:
    run([kaggle_bin(), "kernels", "status", f"{username()}/{KERNEL_SLUG}"])


def cmd_pull(args: argparse.Namespace) -> None:
    target = (
        REPO
        / "artifacts"
        / "kaggle-runs"
        / (args.name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    target.mkdir(parents=True, exist_ok=True)
    run([kaggle_bin(), "kernels", "output", f"{username()}/{KERNEL_SLUG}", "-p", str(target)])
    print(f"downloaded to {target}")
    for bundle in (
        sorted((target / "artifacts" / "bundles").glob("*"))
        if (target / "artifacts" / "bundles").exists()
        else []
    ):
        run(
            [
                sys.executable,
                "-m",
                "hnids.cli",
                "bundle-verify",
                "-c",
                "configs/base.yaml",
                "configs/local.yaml",
                "--path",
                str(bundle),
            ],
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    push = sub.add_parser("push")
    push.add_argument("--extra", nargs="*", default=[], help="extra config files from configs/")
    push.set_defaults(func=cmd_push)
    sub.add_parser("status").set_defaults(func=cmd_status)
    pull = sub.add_parser("pull")
    pull.add_argument("--name", default=None)
    pull.set_defaults(func=cmd_pull)
    args = parser.parse_args()
    os.chdir(REPO)
    args.func(args)


if __name__ == "__main__":
    main()
