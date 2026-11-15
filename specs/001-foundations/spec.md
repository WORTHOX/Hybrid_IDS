# Spec 001: Foundations

## Behaviour
1. `hnids validate-config -c <files...>` merges YAML files from left to right and validates them against the frozen schema. On success it prints `{ok, config_hash}`. On failure it exits with status 2 and prints `{error: {code: CONFIG_INVALID|CONFIG_NOT_FOUND, details}}`.
2. Path containment works as follows:
   - A path is usable only if it resolves inside an authorized root after `..` and symlinks are resolved.
   - A write under a read-only root gives `PATH_NOT_WRITABLE_ROOT`.
   - Anything else outside the roots gives `PATH_OUTSIDE_ROOT`.
3. `write_manifest` and `verify_manifest`: any modified, missing or extra file is rejected with a specific code.
4. `hnids env-report` seeds every RNG, checks the data roots, and writes `run_manifest.json` under `artifact_root/runs/<run_id>/`. The manifest records the git commit, config hash, package versions, lockfile hash and accelerator.
5. Production code logs JSON through structlog. `print` is banned by lint.

## Acceptance
- `uv run poe check` passes: format, lint, strict types, layer contract, tests with ≥90% coverage, bandit and pip-audit.
- Negative tests cover traversal, sibling-prefix, absolute-escape, symlink-escape, read-only-root writes, YAML object tags, unknown keys and a tampered manifest.
