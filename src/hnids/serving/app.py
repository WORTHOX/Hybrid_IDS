"""FastAPI detection service (ADR-0006).

The model bundle is verified and loaded at startup. If that fails, the service stays up but
``/readyz`` returns 503 and every detection endpoint answers ``MODEL_NOT_READY``, so a
partially initialized model never serves predictions.
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from hnids.bundle.package import load_bundle
from hnids.config.schema import ServingConfig
from hnids.errors import ErrorCode, HnidsError
from hnids.logging import get_logger
from hnids.security.paths import AuthorizedRoots
from hnids.serving.engine import InferenceEngine
from hnids.serving.events import (
    BatchRequest,
    DetectionEvent,
    FlowRecord,
    StreamResponse,
    WindowRequest,
)

API_KEY_ENV = "HNIDS_API_KEY"
_STATUS = {
    ErrorCode.INPUT_INVALID: 422,
    ErrorCode.SCHEMA_MISMATCH: 422,
    ErrorCode.BATCH_TOO_LARGE: 413,
    ErrorCode.MODEL_NOT_READY: 503,
}

_log = get_logger("serving")


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "hnids_requests_total", "Requests", ["endpoint", "status"], registry=self.registry
        )
        self.latency = Histogram(
            "hnids_request_seconds",
            "Request latency",
            ["endpoint"],
            buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1.0),
            registry=self.registry,
        )
        self.rejections = Counter(
            "hnids_rejections_total", "Rejected inputs", ["code"], registry=self.registry
        )
        self.sanitized = Counter(
            "hnids_sanitized_values_total",
            "Zero-filled non-finite rate values",
            registry=self.registry,
        )
        self.detections = Counter(
            "hnids_detections_total",
            "Detections",
            ["predicted_class", "kind"],
            registry=self.registry,
        )
        self.confidence = Histogram(
            "hnids_confidence",
            "Prediction confidence",
            buckets=(0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0),
            registry=self.registry,
        )
        self.ready = Gauge(
            "hnids_ready", "1 when a verified bundle is loaded", registry=self.registry
        )
        self.buffers = Gauge(
            "hnids_session_buffers", "Active session buffers", registry=self.registry
        )

    def observe(self, events: list[DetectionEvent]) -> None:
        for event in events:
            self.detections.labels(event.hnids.predicted_class, event.event.kind).inc()
            self.confidence.observe(event.hnids.confidence)
            self.sanitized.inc(event.hnids.sanitized_values)


def _engine(request: Request) -> InferenceEngine:
    current: InferenceEngine | None = request.app.state.engine
    if current is None:
        raise HnidsError("model bundle is not loaded", code=ErrorCode.MODEL_NOT_READY)
    return current


EngineDep = Annotated[InferenceEngine, Depends(_engine)]


class UnauthorizedError(Exception):
    """Missing or wrong API key."""


def _error(
    status: int, code: str, message: str, details: dict[str, object] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def create_app(
    *,
    bundle_dir: Path,
    roots: AuthorizedRoots,
    serving: ServingConfig,
    api_key: str | None = None,
) -> FastAPI:
    metrics = Metrics()
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
    if not key:
        _log.warning("api_key_disabled", reason=f"{API_KEY_ENV} not set; local development only")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = None
        app.state.load_error = None
        try:
            bundle = load_bundle(bundle_dir, roots)
            app.state.engine = InferenceEngine(
                bundle,
                alert_threshold=serving.alert_threshold,
                low_confidence_threshold=serving.low_confidence_threshold,
                buffer_ttl_seconds=serving.buffer_ttl_seconds,
                max_buffers=serving.max_buffers,
            )
            metrics.ready.set(1)
            _log.info("bundle_loaded", version=bundle.metadata.bundle_version)
        except HnidsError as exc:
            app.state.load_error = exc.to_dict()
            metrics.ready.set(0)
            _log.error("bundle_load_failed", **exc.to_dict())
        yield

    app = FastAPI(title="hnids", version="1", lifespan=lifespan)

    # CORS — allow the dashboard frontend to call the API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if key and not (x_api_key and secrets.compare_digest(x_api_key, key)):
            raise UnauthorizedError

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(request: Request, exc: UnauthorizedError) -> JSONResponse:
        metrics.rejections.labels("UNAUTHORIZED").inc()
        return _error(401, "UNAUTHORIZED", "missing or invalid API key")

    @app.exception_handler(HnidsError)
    async def _domain(request: Request, exc: HnidsError) -> JSONResponse:
        metrics.rejections.labels(exc.code.value).inc()
        return _error(_STATUS.get(exc.code, 400), exc.code.value, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        metrics.rejections.labels(ErrorCode.INPUT_INVALID.value).inc()
        errors = [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()
        ][:20]
        return _error(
            422, ErrorCode.INPUT_INVALID.value, "request failed validation", {"errors": errors}
        )

    @app.middleware("http")
    async def _timing(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        path = request.url.path
        metrics.latency.labels(path).observe(time.perf_counter() - started)
        metrics.requests.labels(path, str(response.status_code)).inc()
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        current = request.app.state.engine
        if current is None:
            return JSONResponse(
                status_code=503, content={"ready": False, "error": request.app.state.load_error}
            )
        return JSONResponse(content={"ready": True, "bundle_version": current.meta.bundle_version})

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> Response:
        current = request.app.state.engine
        if current is not None:
            metrics.buffers.set(len(current.buffers))
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/detect", dependencies=[Depends(require_key)])
    def detect(body: WindowRequest, current: EngineDep) -> DetectionEvent:
        events = current.detect_windows([body.flows])
        metrics.observe(events)
        return events[0]

    @app.post("/v1/detect/batch", dependencies=[Depends(require_key)])
    def detect_batch(body: BatchRequest, current: EngineDep) -> list[DetectionEvent]:
        if len(body.windows) > serving.max_batch:
            raise HnidsError(
                f"batch of {len(body.windows)} exceeds max_batch {serving.max_batch}",
                code=ErrorCode.BATCH_TOO_LARGE,
            )
        events = current.detect_windows([w.flows for w in body.windows])
        metrics.observe(events)
        return events

    @app.post("/v1/flows", dependencies=[Depends(require_key)])
    def stream(body: FlowRecord, current: EngineDep) -> StreamResponse:
        count, event = current.push_flow(body)
        if event is not None:
            metrics.observe([event])
        return StreamResponse(buffered=count, window_length=current.seq_len, detection=event)

    return app
