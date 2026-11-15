# ADR-0004: Artifact formats and integrity

- Status: Accepted
- Date: 2026-11-11

## Decision
- No pickle is allowed anywhere, including `joblib` and `torch.save` of whole modules. ruff rules S301 and S403 enforce this.
- Neural weights are stored with `safetensors`. Loading creates the module from the bundle's JSON architecture spec and then loads the state dict.
- XGBoost models are stored in the native `.ubj` format through `save_model` and `load_model`.
- The scaler parameters, class map, feature schema, temperature, config and metrics are stored as JSON.
- Every bundle directory carries a `manifest.json` with the SHA-256 and size of every file, written by `hnids.security.integrity`. `verify_manifest` runs before any file is deserialized. Modified, missing and extra files are all rejected.
- Bundles are versioned as `bundles/<version>/`. An `ACTIVE.json` pointer selects the serving bundle and keeps the previous version for a one-step rollback.

## Consequences
Loading a bundle cannot execute code. A tampered bundle causes the service to fail readiness instead of serving.
