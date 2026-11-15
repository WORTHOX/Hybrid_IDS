# Threat model (STRIDE-lite)

Scope: the training pipeline (local and Kaggle), model bundles and the inference service. This is detection only, with no inline prevention.

| # | Threat | Asset | Mitigation | Verified by |
|---|---|---|---|---|
| T1 | Path traversal or symlink escape through config or API input | Host filesystem | `AuthorizedRoots.resolve` canonicalizes the path and checks containment before opening it. Read and write roots are separate. | `tests/unit/test_paths.py` |
| T2 | Code execution through deserialization (pickle, `torch.load`) | Training and serving host | No pickle anywhere (ruff S301/S403). Weights use safetensors, XGBoost uses its native UBJ/JSON format, and metadata is JSON. YAML is loaded only with `safe_load`. | `test_config.py::test_yaml_tags_cannot_construct_objects`, bundle tests |
| T3 | Artifact tampering or substitution | Model bundle | A SHA-256 manifest covers every file. Verification runs before any load and rejects modified, missing or extra files. The service stays not-ready on failure. | `test_integrity.py`, serving readiness tests |
| T4 | Malformed or oversized input exhausts resources or corrupts state | Service, ingestion | Chunked ingestion, schema validation, quarantine with reason codes, bounded batch size (413), and a bounded number of sequence buffers (LRU + TTL). Invalid flows never reach a buffer. | ingestion and serving tests |
| T5 | Sensitive network identifiers leak through logs or events | IP and flow metadata | Model features exclude identifiers. Events and logs carry a hashed correlation key. Raw IPs are not logged. | event schema tests |
| T6 | Data leakage inflates reported performance | Release decision | Temporal block split with purge gaps, train-only fitting, grouped OOF folds, and a split manifest replayable by hash. | leakage tests |
| T7 | Unauthorized model promotion or rollback | Production behaviour | Bundle activation is an explicit CLI action that is logged with its operator. The service API key comes from the environment. | bundle CLI tests |
| T8 | Credential exposure in the Kaggle workflow | Kaggle account | `kaggle.json` stays in the user profile and is never copied into bundles or the repo. `.gitignore` and gitleaks enforce this. Kernels and the code dataset are private. | CI gitleaks |
| T9 | Supply-chain vulnerabilities | All | `uv.lock` pinning, pip-audit and bandit in CI. | CI |

Residual risks:
- A time-of-check/time-of-use window between `resolve` and `open`, mitigated by least-privilege roots.
- The unauthenticated `/healthz`, which returns no sensitive data.
- WSP is out of scope.
