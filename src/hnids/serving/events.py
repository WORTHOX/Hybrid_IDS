"""Request and DetectionEvent v1 contracts (ADR-0006, FR-9).

Field names follow the Elastic Common Schema where one exists (``event.*``, ``threat.*``);
product-specific fields live under ``hnids.*``. Raw IPs never appear: sessions and flows are
referenced by salted hashes.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_SCHEMA_VERSION = "1"


class FlowRecord(BaseModel):
    """One flow as sent by an exporter. ``features`` uses canonical snake_case names."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=256, description="capture/sensor session key")
    timestamp: datetime
    flow_id: str | None = Field(default=None, max_length=256)
    features: dict[str, float | None]

    @field_validator("features")
    @classmethod
    def _bounded(cls, value: dict[str, float | None]) -> dict[str, float | None]:
        if len(value) > 512:
            raise ValueError("too many features")
        return value


class WindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    flows: list[FlowRecord] = Field(min_length=1, max_length=512)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    windows: list[WindowRequest] = Field(min_length=1)


class EventSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["alert", "signal"]
    category: Literal["intrusion_detection"] = "intrusion_detection"
    start: datetime
    end: datetime
    created: datetime


class ThreatSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    framework: Literal["hnids-taxonomy"] = "hnids-taxonomy"
    technique_name: str


class HnidsSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    predicted_class: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)
    alert_threshold: float
    low_confidence: bool
    is_alert: bool
    bundle_version: str
    feature_schema_version: int
    session_key: str
    correlation_key: str
    validation_status: Literal["ok", "sanitized"]
    sanitized_values: int = Field(ge=0)

    @field_validator("probabilities")
    @classmethod
    def _finite(cls, value: dict[str, float]) -> dict[str, float]:
        if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in value.values()):
            raise ValueError("probabilities must be finite and within [0, 1]")
        return value


class DetectionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    detection_id: str
    event: EventSection
    threat: ThreatSection
    hnids: HnidsSection


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


class StreamResponse(BaseModel):
    buffered: int
    window_length: int
    detection: DetectionEvent | None
