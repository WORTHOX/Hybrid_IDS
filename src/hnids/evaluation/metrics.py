"""Evaluation report: macro and per-class quality, benign false-positive rate, calibration."""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support

ECE_BINS = 15


class ClassMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    precision: float
    recall: float
    f1: float
    support: int
    f1_ci95: tuple[float, float] | None


class EvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    split: str
    windows: int
    accuracy: float
    macro_f1: float
    macro_f1_ci95: tuple[float, float]
    macro_f1_excluding: dict[str, float]
    per_class: dict[str, ClassMetrics]
    confusion: list[list[int]]
    class_names: list[str]
    benign_fpr: float
    benign_fpr_per_capture: dict[str, float]
    benign_fpr_per_hour: dict[str, float]
    ece: float
    brier: float
    temperature: float
    extra: dict[str, float] = {}


def expected_calibration_error(
    probs: np.ndarray, labels: np.ndarray, bins: int = ECE_BINS
) -> float:
    if len(labels) == 0:
        return 0.0
    confidence = probs.max(axis=1)
    correct = probs.argmax(axis=1) == labels
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for lo, hi in itertools.pairwise(edges):
        mask = (confidence > lo) & (confidence <= hi)
        if mask.any():
            ece += mask.mean() * abs(correct[mask].mean() - confidence[mask].mean())
    return float(ece)


def brier_score(probs: np.ndarray, labels: np.ndarray) -> float:
    if len(labels) == 0:
        return 0.0
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(labels)), labels] = 1.0
    return float(((probs - onehot) ** 2).sum(axis=1).mean())


def _macro_f1(truth: np.ndarray, pred: np.ndarray, present: np.ndarray) -> float:
    return float(f1_score(truth, pred, labels=present, average="macro", zero_division=0))


def _bootstrap(
    truth: np.ndarray, pred: np.ndarray, present: np.ndarray, n_classes: int, rounds: int, seed: int
) -> tuple[tuple[float, float], dict[int, tuple[float, float]]]:
    rng = np.random.default_rng(seed)
    macro: list[float] = []
    per_class: list[np.ndarray] = []
    for _ in range(rounds):
        idx = rng.integers(0, len(truth), len(truth))
        t, p = truth[idx], pred[idx]
        macro.append(_macro_f1(t, p, present))
        _, _, f1, _ = precision_recall_fscore_support(
            t, p, labels=np.arange(n_classes), zero_division=0
        )
        per_class.append(f1)
    stacked = np.vstack(per_class)
    lo, hi = np.percentile(macro, [2.5, 97.5])
    cis = {
        c: (float(np.percentile(stacked[:, c], 2.5)), float(np.percentile(stacked[:, c], 97.5)))
        for c in present
    }
    return (float(lo), float(hi)), cis


def _benign_fpr(pred: np.ndarray, truth: np.ndarray, benign: int) -> float:
    mask = truth == benign
    return float((pred[mask] != benign).mean()) if mask.any() else 0.0


def evaluate(
    probs: np.ndarray,
    truth: np.ndarray,
    *,
    split: str,
    class_names: Sequence[str],
    capture_ids: np.ndarray,
    timestamps: np.ndarray,
    temperature: float,
    benign_class: str = "BENIGN",
    exclude_variants: Sequence[str] = ("Rare",),
    bootstrap_rounds: int = 100,
    seed: int = 0,
) -> EvalReport:
    """Score calibrated window probabilities against window labels."""
    n_classes = len(class_names)
    pred = probs.argmax(axis=1)
    present = np.unique(truth)
    precision, recall, f1, support = precision_recall_fscore_support(
        truth, pred, labels=np.arange(n_classes), zero_division=0
    )
    macro_ci, class_ci = (
        _bootstrap(truth, pred, present, n_classes, bootstrap_rounds, seed)
        if len(truth)
        else ((0.0, 0.0), {})
    )
    benign = list(class_names).index(benign_class)

    excluding: dict[str, float] = {}
    for name in exclude_variants:
        if name in class_names:
            keep = present[present != list(class_names).index(name)]
            excluding[name] = _macro_f1(truth, pred, keep) if len(keep) else 0.0

    frame = pd.DataFrame(
        {
            "capture": capture_ids,
            "hour": pd.to_datetime(timestamps).floor("h").strftime("%Y-%m-%d %H:00"),
            "truth": truth,
            "pred": pred,
        }
    )
    benign_rows = frame[frame["truth"] == benign]
    per_capture = (benign_rows["pred"] != benign).groupby(benign_rows["capture"]).mean()
    per_hour = (benign_rows["pred"] != benign).groupby(benign_rows["hour"]).mean()

    return EvalReport(
        split=split,
        windows=len(truth),
        accuracy=float((pred == truth).mean()) if len(truth) else 0.0,
        macro_f1=_macro_f1(truth, pred, present) if len(truth) else 0.0,
        macro_f1_ci95=macro_ci,
        macro_f1_excluding=excluding,
        per_class={
            name: ClassMetrics(
                precision=float(precision[i]),
                recall=float(recall[i]),
                f1=float(f1[i]),
                support=int(support[i]),
                f1_ci95=class_ci.get(i),
            )
            for i, name in enumerate(class_names)
        },
        confusion=confusion_matrix(truth, pred, labels=np.arange(n_classes)).tolist(),
        class_names=list(class_names),
        benign_fpr=_benign_fpr(pred, truth, benign),
        benign_fpr_per_capture={str(k): float(v) for k, v in per_capture.items()},
        benign_fpr_per_hour={str(k): float(v) for k, v in per_hour.items()},
        ece=expected_calibration_error(probs, truth),
        brier=brier_score(probs, truth),
        temperature=temperature,
    )


def render_markdown(report: EvalReport) -> str:
    lines = [
        f"# Evaluation — {report.split}",
        "",
        f"- Windows: {report.windows:,}",
        f"- Macro-F1: **{report.macro_f1:.4f}** "
        f"(95% CI {report.macro_f1_ci95[0]:.4f} to {report.macro_f1_ci95[1]:.4f})",
    ]
    lines += [
        f"- Macro-F1 excluding {k}: **{v:.4f}**" for k, v in report.macro_f1_excluding.items()
    ]
    lines += [
        f"- Accuracy: {report.accuracy:.4f}",
        f"- Benign false-positive rate: {report.benign_fpr:.5f}",
        f"- ECE: {report.ece:.4f}, Brier: {report.brier:.4f}, temperature {report.temperature:.3f}",
    ]
    lines += [f"- {k}: {v:.4f}" for k, v in report.extra.items()]
    lines += [
        "",
        "| Class | Precision | Recall | F1 | F1 95% CI | Support |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in report.per_class.items():
        ci = f"{m.f1_ci95[0]:.3f} to {m.f1_ci95[1]:.3f}" if m.f1_ci95 else "n/a"
        lines.append(
            f"| {name} | {m.precision:.4f} | {m.recall:.4f} | {m.f1:.4f} | {ci} | {m.support:,} |"
        )
    lines += [
        "",
        "## Confusion matrix (rows = truth)",
        "",
        "| | " + " | ".join(report.class_names) + " |",
    ]
    lines.append("|---" * (len(report.class_names) + 1) + "|")
    for name, row in zip(report.class_names, report.confusion, strict=True):
        lines.append(f"| {name} | " + " | ".join(f"{v:,}" for v in row) + " |")
    lines += ["", "## Benign FPR per capture", ""]
    lines += [f"- {k}: {v:.5f}" for k, v in report.benign_fpr_per_capture.items()]
    return "\n".join(lines) + "\n"
