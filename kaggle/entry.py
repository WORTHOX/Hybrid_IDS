"""Kaggle kernel entry point (ADR-0005). Rendered by scripts/kaggle_run.py.

Runs inside a private GPU kernel whose inputs are the CIC-IDS2017 dataset and the private
``hnids-bundle`` dataset (our wheel + configs). Only /kaggle/working is persisted.
"""

import glob
import json
import os
import subprocess
import sys

EXTRA_CONFIGS: list[str] = []  # replaced at render time, e.g. ["smoke.yaml"]
BUNDLE_SLUG = "__BUNDLE_SLUG__"  # replaced at render time


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    wheels = sorted(glob.glob(f"/kaggle/input/**/{BUNDLE_SLUG}/**/hnids-*.whl", recursive=True))
    if not wheels:
        wheels = sorted(glob.glob("/kaggle/input/**/hnids-*.whl", recursive=True))
    if not wheels:
        sys.exit("hnids wheel not found under /kaggle/input")
    wheel = wheels[-1]
    bundle_dir = os.path.dirname(wheel)
    configs_dir = bundle_dir  # configs are uploaded flat next to the wheel
    print("wheel:", wheel, flush=True)

    run(["nvidia-smi"])
    # Keep Kaggle's preinstalled torch/xgboost/numpy; add only what the image lacks.
    run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", wheel])
    run([sys.executable, "-m", "pip", "install", "--quiet", "structlog>=24.1", "safetensors>=0.4"])

    configs = [os.path.join(configs_dir, "base.yaml"), os.path.join(configs_dir, "kaggle.yaml")]
    configs += [os.path.join(configs_dir, name) for name in EXTRA_CONFIGS]
    os.makedirs("/kaggle/working/artifacts", exist_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    cmd = [sys.executable, "-m", "hnids.cli", "train", "-c", *configs]
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    sys.stderr.write(result.stderr[-20000:])
    print(result.stdout, flush=True)
    with open("/kaggle/working/train_result.json", "w", encoding="utf-8") as handle:
        handle.write(result.stdout or json.dumps({"ok": False, "returncode": result.returncode}))
    with open("/kaggle/working/train_stderr.log", "w", encoding="utf-8") as handle:
        handle.write(result.stderr)
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
