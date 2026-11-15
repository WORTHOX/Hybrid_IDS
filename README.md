# hybrid-nids: hybrid network intrusion detection

`hybrid-nids` detects network attacks in sequences of flow records (CICFlowMeter features). Each prediction looks at a **window of 20 consecutive flows from one session**. XGBoost scores each flow, and a **BiGRU + Transformer** reads the whole window and returns a calibrated attack class:

`BENIGN · DoS · DDoS · PortScan · BruteForce · WebAttack · Bot · Rare` (Heartbleed/Infiltration)

The service is a **FastAPI** app that loads a verified model bundle and returns JSON detection events ready for a SIEM.

| Published model `models/b20261111T112951Z` | Value |
|---|---|
| Training | Kaggle 1× Tesla T4, CIC-IDS2017 TrafficLabelling, class-aware temporal block split |
| Test macro-F1 | **0.958** (95% CI 0.942–0.968); 0.961 excluding Rare |
| Benign false-positive rate | 0.056% |
| Per-class F1 | BENIGN 0.999 · DoS 0.997 · DDoS 0.997 · PortScan 1.000 · BruteForce 0.999 · WebAttack 0.964 · Bot 0.772 · Rare 0.933 |
| Full evidence | [`models/b20261111T112951Z/eval_report.md`](models/b20261111T112951Z/eval_report.md), [`release/b20261111T112951Z.md`](release/b20261111T112951Z.md) |

> About 30% of CIC-IDS2017 test rows are exact copies of training rows, which flatters every result on this dataset. Validate on your own traffic before relying on the numbers.

---

## 1. Install

You need Python 3.11+ and git. Clone the repo, then install from the repo root.

```bash
git clone https://github.com/aadilshaikh123/hybrid-nids.git
cd hybrid-nids

pip install uv                      # once
uv sync --extra ml --extra serve    # creates .venv with torch (CPU), xgboost, fastapi
```

Without uv, use `python -m venv .venv`, activate it, then `pip install -e ".[ml,serve]"`.

## 2. Check the model

The model ships in [`models/b20261111T112951Z/`](models/b20261111T112951Z/). Every file is covered by a SHA-256 manifest, and the service refuses to load a bundle whose files were modified.

```bash
uv run hnids bundle-verify -c configs/base.yaml configs/local.yaml --path models/b20261111T112951Z
```
Expected output:
```json
{ "ok": true, "verified": true, "bundle_version": "b20261111T112951Z",
  "metrics": { "test_macro_f1": 0.9578, "test_benign_fpr": 0.00056, "...": "..." } }
```

## 3. Start the API

Set an API key, then start the server. Every `/v1/*` call must send this key in the `X-API-Key` header.

**bash / Git Bash / macOS / Linux**
```bash
export HNIDS_API_KEY=change-me
uv run hnids serve -c configs/base.yaml configs/local.yaml --bundle models/b20261111T112951Z
```
**Windows PowerShell**
```powershell
$env:HNIDS_API_KEY = "change-me"
uv run hnids serve -c configs/base.yaml configs/local.yaml --bundle models/b20261111T112951Z
```

The server listens on `http://127.0.0.1:8000`; host and port are set under `serving:` in `configs/base.yaml`. Interactive API docs are at **http://127.0.0.1:8000/docs**.

Check that it's ready:
```bash
curl http://127.0.0.1:8000/readyz
# {"ready":true,"bundle_version":"b20261111T112951Z"}
```
If the bundle fails verification, `/readyz` returns **503** with the error, and every detection call returns `MODEL_NOT_READY`.

### Optional: manage models with a registry and rollback
```bash
uv run hnids bundle-import   -c configs/base.yaml configs/local.yaml --path models/b20261111T112951Z
uv run hnids bundle-activate -c configs/base.yaml configs/local.yaml --version b20261111T112951Z
uv run hnids serve           -c configs/base.yaml configs/local.yaml        # serves the ACTIVE bundle
uv run hnids bundle-rollback -c configs/base.yaml configs/local.yaml        # back to the previous one
```

## 4. Send inputs

### Input format

A **flow** is one CICFlowMeter record:

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | Capture or sensor session. A window must come from one session. |
| `timestamp` | ISO-8601 | Flow time. Flows in a window must be in chronological order. |
| `flow_id` | string, optional | Your id. It is returned only as a salted hash (`correlation_key`). |
| `features` | object | **All 77** numeric CICFlowMeter features, using snake_case names. |

The 77 feature names are listed in [`src/hnids/ingestion/schema.py`](src/hnids/ingestion/schema.py) (`RAW_FEATURE_COLUMNS`). Some examples are `protocol`, `flow_duration`, `total_fwd_packets`, `flow_bytes_s` and `init_win_bytes_forward`. They are CICFlowMeter's column names, lower-cased, with non-alphanumerics replaced by `_`.

`flow_bytes_s` and `flow_packets_s` may be `null`, for example when a zero-duration flow gives an infinite rate. They are zero-filled and flagged (`validation_status: "sanitized"`). Any other missing or non-finite feature makes the request invalid.

One flow, shortened from [`examples/detect_ddos.request.json`](examples/detect_ddos.request.json):
```json
{
  "session_id": "Friday-WorkingHours-Afternoon-DDos",
  "timestamp": "2017-07-07T16:15:00+00:00",
  "flow_id": "ddos-flow-00",
  "features": {
    "protocol": 6.0,
    "flow_duration": 3446946.0,
    "total_fwd_packets": 4.0,
    "total_backward_packets": 0.0,
    "total_length_of_fwd_packets": 24.0,
    "...": "all 77 features"
  }
}
```

[`examples/`](examples/) holds complete, ready-to-send requests built from **real held-out test traffic**, each paired with the model's actual response.

| Request | Window's true class | Response |
|---|---|---|
| `detect_benign.request.json` | BENIGN | `BENIGN`, 0.9997, kind `signal` |
| `detect_ddos.request.json` | DDoS | `DDoS`, 1.0000, kind `alert` |
| `detect_dos.request.json` | DoS | `DoS`, 0.9999, kind `alert` |
| `detect_portscan.request.json` | PortScan | `PortScan`, 0.9993, kind `alert` |
| `detect_webattack.request.json` | WebAttack | `WebAttack`, 0.9997, kind `alert` |
| `detect_batch.request.json` | BENIGN + DDoS | `[BENIGN, DDoS]` |
| `stream_flows.request.json` | 20 PortScan flows sent one by one | detection on the 20th flow: `PortScan` |
| `detect_invalid.request.json` | `flow_duration` set to `null` | HTTP 422, `INPUT_INVALID` |

### a) One window: `POST /v1/detect`
The body is `{"flows": [ ...exactly 20 flows... ]}`.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/detect \
  -H "X-API-Key: $HNIDS_API_KEY" -H "Content-Type: application/json" \
  --data @examples/detect_ddos.request.json
```
PowerShell: `curl.exe -s -X POST http://127.0.0.1:8000/v1/detect -H "X-API-Key: $env:HNIDS_API_KEY" -H "Content-Type: application/json" --data "@examples/detect_ddos.request.json"`

**Expected output** ([`examples/detect_ddos.response.json`](examples/detect_ddos.response.json)). `detection_id`, `created` and the hash keys differ on every call; the scores are deterministic.
```json
{
  "schema_version": "1",
  "detection_id": "cf234788-90c6-4935-b70d-b1d30c178cb3",
  "event": {
    "kind": "alert",
    "category": "intrusion_detection",
    "start": "2017-07-07T16:15:00Z",
    "end": "2017-07-07T16:15:00Z",
    "created": "2026-11-11T13:02:25.602986Z"
  },
  "threat": { "framework": "hnids-taxonomy", "technique_name": "DDoS" },
  "hnids": {
    "predicted_class": "DDoS",
    "probabilities": { "BENIGN": 0.0, "DoS": 0.0, "DDoS": 0.999994, "PortScan": 0.0,
                       "BruteForce": 0.0, "WebAttack": 0.0, "Bot": 2e-06, "Rare": 4e-06 },
    "confidence": 0.9999935116558399,
    "alert_threshold": 0.5,
    "low_confidence": false,
    "is_alert": true,
    "bundle_version": "b20261111T112951Z",
    "feature_schema_version": 1,
    "session_key": "fc12b050e24a404d",
    "correlation_key": "225626bec72db12f",
    "validation_status": "ok",
    "sanitized_values": 0
  }
}
```

How to read it:

| Field | Meaning |
|---|---|
| `predicted_class` | The most likely class. |
| `confidence` | Its calibrated probability. |
| `is_alert` / `event.kind` | `true` / `"alert"` when the class is not BENIGN and confidence ≥ `alert_threshold`. Otherwise `"signal"`. |
| `low_confidence` | `true` when confidence < 0.6. Route these to manual review. |
| `session_key`, `correlation_key` | Salted hashes, so raw session and flow ids never leave the service. |

The full contract is in [`docs/schemas/detection-event.v1.json`](docs/schemas/detection-event.v1.json).

### b) Several windows: `POST /v1/detect/batch`
The body is `{"windows": [ {"flows": [...20...]}, ... ]}`, with at most `serving.max_batch` windows (default 256; more returns HTTP 413). The response is a list of events in the same order. Example: [`examples/detect_batch.request.json`](examples/detect_batch.request.json) → `[BENIGN, DDoS]`.

### c) Streaming one flow at a time: `POST /v1/flows`
Send single flow objects as they arrive. The service keeps a bounded buffer per `session_id` and returns a detection once 20 flows have arrived. After that it returns one detection for every new flow, over a sliding window.
```json
{"buffered": 1,  "window_length": 20, "detection": null}
...
{"buffered": 20, "window_length": 20, "detection": { "...DetectionEvent...": "", "hnids": {"predicted_class": "PortScan"} }}
```
A session's buffer resets after 300 s idle or when a flow arrives out of time order. See [`examples/stream_flows.responses.json`](examples/stream_flows.responses.json).

### d) Python client
```python
import json, httpx

window = json.load(open("examples/detect_portscan.request.json"))
r = httpx.post("http://127.0.0.1:8000/v1/detect", json=window, headers={"X-API-Key": "change-me"})
event = r.json()
print(event["hnids"]["predicted_class"], event["hnids"]["confidence"])
# PortScan 0.9993...
```

### Errors
Errors are returned as `{"error": {"code", "message", "details"}}`. An invalid request **never** produces a detection.

| HTTP | `code` | When |
|---|---|---|
| 401 | `UNAUTHORIZED` | `X-API-Key` missing or wrong |
| 413 | `BATCH_TOO_LARGE` | Batch exceeds `max_batch` |
| 422 | `INPUT_INVALID` | Wrong window length, mixed sessions, unsorted timestamps, missing or non-finite feature, or malformed JSON |
| 422 | `SCHEMA_MISMATCH` | Missing or unknown feature names |
| 503 | `MODEL_NOT_READY` | Bundle missing or failed integrity verification |

Example ([`examples/detect_invalid.response.json`](examples/detect_invalid.response.json)):
```json
{"error": {"code": "INPUT_INVALID", "message": "feature 'flow_duration' is missing or non-finite", "details": {"feature": "flow_duration"}}}
```

## 5. Operate
- `GET /healthz`: liveness.
- `GET /readyz`: model loaded and verified.
- `GET /metrics`: Prometheus metrics covering requests, latency histograms, rejections by code, detections by class, confidence distribution, active session buffers and `hnids_ready`.
- Measured on the reference laptop CPU (see the [release report](release/b20261111T112951Z.md)):
  - about 12.9 ms mean per window end to end;
  - about 65 windows/s (1,300 flows/s) on one worker;
  - 0 errors in the load test.

  The PRD's 5 ms target is not met on this hardware and is recorded as a release deviation.

## More
- Training on Kaggle GPU, rollback and release evidence: [docs/runbook.md](docs/runbook.md)
- Architecture: [docs/architecture.md](docs/architecture.md); decision records: [docs/adr/](docs/adr/); threat model: [docs/threat-model.md](docs/threat-model.md)
- Product requirements: [docs/prd.md](docs/prd.md). Engineering rules: [constitution.md](constitution.md)
- Regenerate the examples: `uv run python scripts/make_examples.py --bundle models/<version> --run-dir <run>/artifacts/runs/<id>`
- Dataset: CIC-IDS2017, Canadian Institute for Cybersecurity, University of New Brunswick. Cite the original work when you publish results.
