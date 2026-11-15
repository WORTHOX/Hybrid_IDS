# ADR-0006: Serving interface and detection event schema

- Status: Accepted
- Date: 2026-11-11

## Decision
- **Interface:** a FastAPI REST service run with uvicorn. It has these endpoints:
  - `POST /v1/detect`: one window.
  - `POST /v1/detect/batch`: bounded by `serving.max_batch`; larger requests get 413.
  - `POST /v1/flows`: streaming flows into per-capture buffers.
  - `GET /healthz`, `/readyz`, `/metrics` (Prometheus).
- **Auth:** an `X-API-Key` header checked against the `HNIDS_API_KEY` environment variable. When the variable is unset, auth is disabled for local development only and a warning is logged.
- **Event:** `DetectionEvent` v1 is JSON whose field names follow ECS where one exists (`event.*`, `threat.*`), with an `hnids.*` namespace for the rest. Its schema is `docs/schemas/detection-event.v1.json`.
- **Errors:** `{"error": {"code", "message", "details"}}` with a stable `ErrorCode`. Invalid input never yields a detection.
