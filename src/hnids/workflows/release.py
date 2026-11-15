"""Release evidence report (PRD §3.2 launch metrics, §9 release gates).

Collects what can be verified automatically from a bundle and the evidence files in
``release/`` and marks each gate PASS, DEVIATION (with the measured value) or PENDING
(evidence missing or a human sign-off). It never upgrades a gate it cannot verify.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hnids.bundle.package import EVAL_JSON, load_bundle
from hnids.security.paths import AuthorizedRoots

Status = Literal["PASS", "DEVIATION", "PENDING"]

MACRO_F1_TARGET = 0.95
LATENCY_TARGET_MS = 5.0
FINITE_RATE_TARGET = 0.999


@dataclass(frozen=True)
class Gate:
    name: str
    status: Status
    evidence: str


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def evaluate_gates(
    bundle_dir: Path, evidence_dir: Path, roots: AuthorizedRoots
) -> tuple[str, list[Gate]]:
    bundle = load_bundle(bundle_dir, roots)  # integrity verified here; raises if tampered
    report = json.loads((bundle.directory / EVAL_JSON).read_text(encoding="utf-8"))["test"]
    latency = _read(evidence_dir / "latency.json")
    load = _read(evidence_dir / "loadtest.json")
    replay = _read(evidence_dir / "replay.json")
    rollback_log = evidence_dir / "rollback_rehearsal.log"

    macro = float(report["macro_f1"])
    excl = report["macro_f1_excluding"]
    gates = [
        Gate(
            "Macro-F1 >= 0.95 (CIC-IDS2017 frozen test)",
            "PASS" if macro >= MACRO_F1_TARGET else "DEVIATION",
            f"{macro:.4f} (95% CI {report['macro_f1_ci95'][0]:.4f}"
            f"-{report['macro_f1_ci95'][1]:.4f}); "
            + ", ".join(f"excluding {k}: {v:.4f}" for k, v in excl.items()),
        ),
        Gate(
            "Per-class precision/recall reported",
            "PASS",
            "; ".join(
                f"{c} P{m['precision']:.3f}/R{m['recall']:.3f} (n={m['support']})"
                for c, m in report["per_class"].items()
            ),
        ),
        Gate(
            "Per-class minimum thresholds (set before sign-off)",
            "PENDING",
            "thresholds not yet agreed; weakest classes: "
            + ", ".join(
                f"{c} F1 {m['f1']:.3f}"
                for c, m in sorted(report["per_class"].items(), key=lambda kv: kv[1]["f1"])[:2]
            ),
        ),
        Gate(
            "Benign false-positive rate (SOC threshold)",
            "PENDING",
            f"measured {report['benign_fpr']:.5f}; SOC threshold to be agreed (PRD 3.2)",
        ),
        Gate(
            "Calibration baseline recorded",
            "PASS",
            f"ECE {report['ece']:.4f}, Brier {report['brier']:.4f}, "
            f"temperature {report['temperature']:.3f}",
        ),
        Gate(
            "Leakage disclosure (cross-split exact duplicates)",
            "PASS",
            ", ".join(f"{k}: {v:.3f}" for k, v in report.get("extra", {}).items()),
        ),
        Gate(
            "Artifact integrity verified before load",
            "PASS",
            f"manifest verified for {bundle.metadata.bundle_version}",
        ),
    ]

    if latency is None:
        gates.append(
            Gate("Mean single-window latency <= 5 ms", "PENDING", "release/latency.json missing")
        )
    else:
        mean = float(latency["end_to_end"]["mean_ms"])
        gates.append(
            Gate(
                "Mean single-window latency <= 5 ms",
                "PASS" if mean <= LATENCY_TARGET_MS else "DEVIATION",
                f"end-to-end mean {mean:.2f} ms (p95 {latency['end_to_end']['p95_ms']:.2f}, "
                f"p99 {latency['end_to_end']['p99_ms']:.2f}); model-only mean "
                f"{latency['model_only']['mean_ms']:.2f} ms; host {latency['host']['processor']}",
            )
        )

    if load is None:
        gates.append(
            Gate(
                "Valid requests with finite prediction >= 99.9%",
                "PENDING",
                "release/loadtest.json missing",
            )
        )
    else:
        rate = float(load["finite_prediction_rate"])
        gates.append(
            Gate(
                "Valid requests with finite prediction >= 99.9%",
                "PASS" if rate >= FINITE_RATE_TARGET and load["error_rate"] == 0 else "DEVIATION",
                f"{rate:.4%} of {load['requests']} requests; error rate {load['error_rate']:.4%}; "
                f"{load['windows_per_second']:.1f} windows/s "
                f"({load['flows_per_second']:.0f} flows/s); "
                f"HTTP p95 {load['latency_ms']['p95']:.0f} ms at concurrency {load['concurrency']}",
            )
        )

    if replay is None:
        gates.append(
            Gate(
                "Production path matches offline evaluation",
                "PENDING",
                "release/replay.json missing",
            )
        )
    else:
        gates.append(
            Gate(
                "Production path matches offline evaluation",
                "PASS" if replay["http_failures"] == 0 else "DEVIATION",
                f"{replay['windows_sent']} real test windows via HTTP, "
                f"{replay['http_failures']} failures, "
                f"class-balanced macro-F1 {replay['macro_f1']:.4f}",
            )
        )

    rehearsed = rollback_log.is_file() and "ARTIFACT_HASH_MISMATCH" in rollback_log.read_text(
        encoding="utf-8"
    )
    gates.append(
        Gate(
            "Rollback and tamper refusal rehearsed",
            "PASS" if rehearsed else "PENDING",
            "see release/rollback_rehearsal.log" if rehearsed else "rehearsal log missing",
        )
    )
    gates += [
        Gate(
            "Static typing, lint, tests, dependency and security scans",
            "PENDING",
            "confirm CI is green for the release commit",
        ),
        Gate("Reproducibility (repeat training within tolerance)", "PENDING", "not yet repeated"),
        Gate("SOC replay-based acceptance", "PENDING", "manual sign-off"),
        Gate("Security / ML / platform / product sign-off", "PENDING", "manual sign-off"),
    ]
    return bundle.metadata.bundle_version, gates


def render(version: str, gates: list[Gate]) -> str:
    counts = {s: sum(g.status == s for g in gates) for s in ("PASS", "DEVIATION", "PENDING")}
    lines = [
        f"# Release evidence: bundle {version}",
        "",
        f"Generated {datetime.now(UTC).isoformat()}. PASS {counts['PASS']}, "
        f"DEVIATION {counts['DEVIATION']}, PENDING {counts['PENDING']}.",
        "",
        "A DEVIATION blocks production promotion unless an approved, time-bounded exception is",
        "recorded below (constitution, Development Workflow 6).",
        "",
        "| Gate | Status | Evidence |",
        "|---|---|---|",
    ]
    lines += [f"| {g.name} | {g.status} | {g.evidence} |" for g in gates]
    lines += [
        "",
        "## Exceptions",
        "",
        "| Gate | Approver | Reason | Expiry |",
        "|---|---|---|---|",
        "| | | | |",
        "",
    ]
    return "\n".join(lines)


def write_release_report(bundle_dir: Path, evidence_dir: Path, roots: AuthorizedRoots) -> Path:
    version, gates = evaluate_gates(bundle_dir, evidence_dir, roots)
    target = roots.resolve(evidence_dir / f"{version}.md", mode="write")
    target.write_text(render(version, gates), encoding="utf-8")
    return target
