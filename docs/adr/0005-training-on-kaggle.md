# ADR-0005: Training on Kaggle GPUs

- Status: Accepted
- Date: 2026-11-11

## Context
There is no local GPU. Kaggle provides free GPU sessions (P100 or T4) of up to 12 hours each, with a weekly quota, and the dataset is already hosted there.

## Decision
- `scripts/kaggle_run.py` drives each training stage locally through the official Kaggle CLI:
  1. Build the wheel.
  2. Publish the wheel, configs and `kaggle/entry.py` as a **private** dataset (`<user>/hnids-bundle`).
  3. Push a **private** script kernel with GPU and internet enabled. Its sources are `pshikk/cicids2017-untampered` and the bundle dataset.
  4. Poll the kernel status.
  5. Download `/kaggle/working` outputs into `artifacts/kaggle-runs/<run>/`.
  6. Verify the integrity manifests.
- The kernel installs the wheel on top of Kaggle's preinstalled torch and xgboost. The actual versions are recorded in the run manifest.
- Outputs are written only under `/kaggle/working`, which is the only writable root in `configs/kaggle.yaml`.
- Training checkpoints after every epoch, because of the session limit.
- Kaggle hardware is training hardware only. Latency is measured on the serving reference ([ADR-0007](0007-reference-hardware-and-latency.md)).

## Consequences
- Results depend on Kaggle's image versions, which are captured in the manifest.
- GPU kernels need a phone-verified Kaggle account.
- Credentials (Kaggle OAuth login in `~/.kaggle/credentials.json`, from `kaggle auth login`) never leave the developer machine. Legacy `kaggle.json` keys are no longer accepted for uploads by Kaggle CLI 2.x.
