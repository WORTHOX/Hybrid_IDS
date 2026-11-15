# Architecture

## Purpose
`hnids` classifies windows of 20 consecutive network flows from one capture/session as benign or as an attack class. It returns calibrated probabilities and an audit-ready detection event. See the [PRD](prd.md) and the [constitution](../constitution.md).

## Data flow
```
raw CSV (TrafficLabelling) ─► ingestion ─► splitting ─► preprocessing ─► sequencing
                                │  quarantine + quality report    │ train-only scaler
                                ▼                                  ▼
                          per-capture parquet           XGBoost OOF probs (models)
                                                                   │ fused features (B, 20, F+C)
                                                                   ▼
                                                  BiGRU→Transformer (models) ◄─ training
                                                                   │
                                                  evaluation + temperature calibration
                                                                   │
                                                     bundle (versioned, sha256 manifest)
                                                                   │
                                                  serving (FastAPI) ─► DetectionEvent v1
```

## Module boundaries (enforced by import-linter)
Higher layers may import lower ones, but never the other way round.

| Layer | Module | Responsibility |
|---|---|---|
| 14 | `hnids.cli` | Command-line entry points. Holds no domain logic. |
| 13 | `hnids.serving` | Inference engine, sequence buffers, FastAPI app, metrics |
| 12 | `hnids.bundle` | Package, verify, load, activate and roll back model bundles |
| 11 | `hnids.evaluation` | Metrics, calibration, reports |
| 10 | `hnids.training` | Hybrid model training loop, checkpoints |
| 9 | `hnids.models` | XGBoost OOF wrapper, `HybridModel` |
| 8 | `hnids.sequencing` | Boundary-respecting window construction |
| 7 | `hnids.preprocessing` | Train-only scaler, persisted as JSON |
| 6 | `hnids.splitting` | Temporal block split, split manifest |
| 5 | `hnids.ingestion` | Chunked reading, normalization, sanitation, quarantine |
| 4 | `hnids.reproducibility` | Seeding, run manifests |
| 3 | `hnids.security` | Authorized roots, integrity manifests |
| 2 | `hnids.config` | Immutable validated configuration |
| 1 | `hnids.logging`, `hnids.errors` | Structured logs, machine-readable errors |

## Environments
Each environment is a config profile layered on `configs/base.yaml`.

| Profile | Read roots | Write roots |
|---|---|---|
| `local.yaml` | `data/raw` | `artifacts/`, `quarantine/` |
| `kaggle.yaml` | `/kaggle/input` | `/kaggle/working/...` |

Kaggle is used only for training ([ADR-0005](adr/0005-training-on-kaggle.md)).

## Quality gate
Run `uv run poe check`. It runs ruff format, ruff lint, mypy --strict, import-linter, pytest with ≥90% coverage, bandit and pip-audit. CI runs the same command on Ubuntu and Windows, plus gitleaks.
