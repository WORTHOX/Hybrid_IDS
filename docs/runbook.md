# Runbook

All commands run from the repository root. `-c configs/base.yaml configs/local.yaml` selects the local profile.

## 1. Setup
```bash
pip install uv                 # once
uv sync --all-extras           # Python 3.11 env with ml + serve extras (CPU torch locally)
uv run poe check               # format, lint, strict types, layers, tests, bandit, pip-audit
```

## 2. Train on Kaggle GPU (ADR-0005)
```bash
uv tool install kaggle --with six
kaggle auth login              # OAuth; a stale ~/.kaggle/kaggle.json shadows it, so rename that file
python scripts/kaggle_run.py push --extra smoke.yaml   # quick end-to-end check (~3 min)
python scripts/kaggle_run.py push                      # full run (~40-65 min on a T4)
python scripts/kaggle_run.py status
python scripts/kaggle_run.py pull --name <run-name>    # downloads outputs and verifies the bundle
```
- The kernel is private, runs on a T4, and fails fast if no GPU is present (`training.require_gpu`).
- Your laptop can be off while it runs.
- Outputs land in `artifacts/kaggle-runs/<run-name>/artifacts/{bundles,runs}`.

## 3. Register, activate and roll back a model
```bash
uv run hnids bundle-import   -c ... --path artifacts/kaggle-runs/<run>/artifacts/bundles/<version>
uv run hnids bundle-activate -c ... --version <version>   # verifies integrity first
uv run hnids bundle-rollback -c ...                        # one step back to the previous bundle
```
`artifacts/bundles/ACTIVE.json` records the active and previous versions, plus who changed them and when. Restart the service after any change.

## 4. Serve
```bash
export HNIDS_API_KEY=<secret>
uv run hnids serve -c configs/base.yaml configs/local.yaml            # http://127.0.0.1:8000
curl localhost:8000/readyz
```
| Endpoint | Purpose |
|---|---|
| `POST /v1/detect` | Score one window of 20 flows from one session |
| `POST /v1/detect/batch` | Score several windows (limit `serving.max_batch`) |
| `POST /v1/flows` | Stream single flows; returns a detection once a session's window is full |
| `GET /healthz`, `/readyz`, `/metrics` | Liveness, readiness (503 if the bundle failed verification), Prometheus |

The event contract is `docs/schemas/detection-event.v1.json`. Errors return `{"error": {"code", "message", "details"}}`.

## 5. Release evidence
```bash
uv run python scripts/replay_test_windows.py --run-dir <run>/artifacts/runs/<id> --api-key $HNIDS_API_KEY --out release/replay.json
uv run python scripts/latency_bench.py --bundle artifacts/bundles/<version> --out release/latency.json
uv run python scripts/loadtest.py --seconds 60 --concurrency 16 --api-key $HNIDS_API_KEY --out release/loadtest.json
uv run hnids release-report -c configs/base.yaml configs/local.yaml   # writes release/<version>.md
```
Each gate is marked PASS, DEVIATION or PENDING. A DEVIATION blocks promotion unless an exception is recorded in the report.
