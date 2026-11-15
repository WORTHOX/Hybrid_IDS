"""Inference engine: validated flows -> fused rows -> calibrated probabilities -> events.

Uses exactly the bundle's scaler, XGBoost model, hybrid weights, temperature and class map
(FR-9). Per-flow work (validation, scaling, XGBoost) happens once per flow; the sequence model
runs once per window.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import torch

from hnids.bundle.package import LoadedBundle
from hnids.errors import ErrorCode, InputValidationError
from hnids.ingestion.schema import RATE_FEATURES, RATE_NONFINITE, RAW_FEATURE_COLUMNS
from hnids.serving.buffers import SessionBuffers, WindowReady
from hnids.serving.events import (
    DetectionEvent,
    EventSection,
    FlowRecord,
    HnidsSection,
    ThreatSection,
)


@dataclass(frozen=True)
class PreparedFlow:
    row: np.ndarray  # fused (F + C,) float32
    sanitized: int


def _digest(salt: str, value: str) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:16]


class InferenceEngine:
    def __init__(
        self,
        bundle: LoadedBundle,
        *,
        alert_threshold: float | None = None,
        low_confidence_threshold: float | None = None,
        correlation_salt: str = "",
        buffer_ttl_seconds: float = 300.0,
        max_buffers: int = 10_000,
    ) -> None:
        meta = bundle.metadata
        expected = (*RAW_FEATURE_COLUMNS, RATE_NONFINITE)
        if tuple(meta.feature_names) != expected:
            raise InputValidationError(
                "bundle feature schema differs from runtime", code=ErrorCode.ARTIFACT_INCOMPATIBLE
            )
        self.bundle = bundle
        self.meta = meta
        self.classes = list(meta.class_names)
        self.benign = self.classes.index(meta.benign_class)
        self.alert_threshold = meta.alert_threshold if alert_threshold is None else alert_threshold
        self.low_confidence_threshold = (
            meta.low_confidence_threshold
            if low_confidence_threshold is None
            else low_confidence_threshold
        )
        self.salt = correlation_salt or meta.bundle_version
        self.seq_len = meta.seq_len
        self.buffers = SessionBuffers(
            self.seq_len, ttl_seconds=buffer_ttl_seconds, max_sessions=max_buffers
        )
        self.model = bundle.model.eval()

    # ------------------------------------------------------------------ per-flow
    def _raw_vector(self, flow: FlowRecord) -> tuple[np.ndarray, int]:
        missing = [c for c in RAW_FEATURE_COLUMNS if c not in flow.features]
        unknown = sorted(set(flow.features) - set(RAW_FEATURE_COLUMNS))
        if missing or unknown:
            raise InputValidationError(
                "flow features do not match the schema",
                code=ErrorCode.SCHEMA_MISMATCH,
                details={"missing": missing[:10], "unknown": unknown[:10]},
            )
        values = np.empty(len(RAW_FEATURE_COLUMNS) + 1, dtype=np.float64)
        sanitized = 0
        for i, name in enumerate(RAW_FEATURE_COLUMNS):
            value = flow.features[name]
            finite = value is not None and math.isfinite(value)
            if name in RATE_FEATURES and not finite:
                values[i] = 0.0
                sanitized += 1
            elif not finite:
                raise InputValidationError(
                    f"feature {name!r} is missing or non-finite",
                    code=ErrorCode.INPUT_INVALID,
                    details={"feature": name},
                )
            else:
                values[i] = float(value)  # type: ignore[arg-type]
        values[-1] = 1.0 if sanitized else 0.0
        return values, sanitized

    def prepare_flows(self, flows: Sequence[FlowRecord]) -> list[PreparedFlow]:
        """Validate, scale and attach XGBoost probabilities (vectorized across flows)."""
        raws = [self._raw_vector(f) for f in flows]
        matrix = np.vstack([r for r, _ in raws])
        scaled = self.bundle.scaler.transform_array(matrix)
        probs = self.bundle.xgb.predict_proba(scaled)
        fused = np.concatenate([scaled, probs], axis=1).astype(np.float32)
        return [PreparedFlow(row=fused[i], sanitized=raws[i][1]) for i in range(len(flows))]

    # ------------------------------------------------------------------ per-window
    @torch.inference_mode()
    def _probabilities(self, windows: np.ndarray) -> np.ndarray:
        logits = self.model(torch.from_numpy(windows)).double().numpy()
        scaled = logits / self.meta.temperature
        scaled -= scaled.max(axis=1, keepdims=True)
        exp = np.exp(scaled)
        probs: np.ndarray = exp / exp.sum(axis=1, keepdims=True)
        if not np.isfinite(probs).all():
            raise InputValidationError(
                "model produced non-finite output", code=ErrorCode.INPUT_INVALID
            )
        return probs

    def _event(
        self,
        probs: np.ndarray,
        *,
        session: str,
        flow_id: str | None,
        start: datetime,
        end: datetime,
        sanitized: int,
    ) -> DetectionEvent:
        index = int(probs.argmax())
        confidence = float(probs[index])
        is_alert = index != self.benign and confidence >= self.alert_threshold
        return DetectionEvent(
            detection_id=str(uuid.uuid4()),
            event=EventSection(
                kind="alert" if is_alert else "signal",
                start=start,
                end=end,
                created=datetime.now(UTC),
            ),
            threat=ThreatSection(technique_name=self.classes[index]),
            hnids=HnidsSection(
                predicted_class=self.classes[index],
                probabilities={
                    c: float(round(p, 6)) for c, p in zip(self.classes, probs, strict=True)
                },
                confidence=min(max(confidence, 0.0), 1.0),
                alert_threshold=self.alert_threshold,
                low_confidence=confidence < self.low_confidence_threshold,
                is_alert=is_alert,
                bundle_version=self.meta.bundle_version,
                feature_schema_version=self.meta.feature_schema_version,
                session_key=_digest(self.salt, session),
                correlation_key=_digest(self.salt, f"{session}|{flow_id or end.isoformat()}"),
                validation_status="sanitized" if sanitized else "ok",
                sanitized_values=sanitized,
            ),
        )

    def _check_window(self, flows: Sequence[FlowRecord]) -> None:
        if len(flows) != self.seq_len:
            raise InputValidationError(
                f"a window needs exactly {self.seq_len} flows, got {len(flows)}",
                code=ErrorCode.INPUT_INVALID,
            )
        if len({f.session_id for f in flows}) != 1:
            raise InputValidationError(
                "a window must contain flows from a single session", code=ErrorCode.INPUT_INVALID
            )
        times = [f.timestamp for f in flows]
        if any(b < a for a, b in itertools.pairwise(times)):
            raise InputValidationError(
                "flows in a window must be in chronological order", code=ErrorCode.INPUT_INVALID
            )

    def detect_windows(self, windows: Sequence[Sequence[FlowRecord]]) -> list[DetectionEvent]:
        """Score complete windows. All windows are validated before any is scored."""
        for flows in windows:
            self._check_window(flows)
        flat = [f for flows in windows for f in flows]
        prepared = self.prepare_flows(flat)
        rows = np.stack([p.row for p in prepared]).reshape(len(windows), self.seq_len, -1)
        probs = self._probabilities(rows)
        events = []
        for i, flows in enumerate(windows):
            chunk = prepared[i * self.seq_len : (i + 1) * self.seq_len]
            events.append(
                self._event(
                    probs[i],
                    session=flows[-1].session_id,
                    flow_id=flows[-1].flow_id,
                    start=flows[0].timestamp,
                    end=flows[-1].timestamp,
                    sanitized=sum(p.sanitized for p in chunk),
                )
            )
        return events

    def push_flow(self, flow: FlowRecord) -> tuple[int, DetectionEvent | None]:
        """Streaming: buffer one flow; emit an event once the session window is full.
        Invalid flows raise before touching any buffer."""
        prepared = self.prepare_flows([flow])[0]
        count, ready = self.buffers.push(
            flow.session_id, prepared.row, flow.timestamp, sanitized=prepared.sanitized
        )
        if ready is None:
            return count, None
        return count, self._from_ready(ready, flow)

    def _from_ready(self, ready: WindowReady, flow: FlowRecord) -> DetectionEvent:
        probs = self._probabilities(ready.rows[None, :, :].astype(np.float32))[0]
        return self._event(
            probs,
            session=flow.session_id,
            flow_id=flow.flow_id,
            start=ready.start,
            end=ready.end,
            sanitized=ready.sanitized,
        )
