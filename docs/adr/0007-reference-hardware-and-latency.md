# ADR-0007: Reference hardware and latency measurement

- Status: Accepted (provisional reference)
- Date: 2026-11-11

## Decision
- The provisional reference is the developer workstation: Windows 11, CPU inference. It is replaced when production hardware is named.
- Two latencies are reported, each as mean, p50, p95 and p99:
  - **model-only:** fused tensor → calibrated probabilities.
  - **end-to-end:** validated JSON window → `DetectionEvent`, covering preprocessing, XGBoost, the network and event construction, but not HTTP.
- HTTP-level latency and throughput come from `scripts/loadtest.py`.
- The 5 ms mean target from the PRD is evaluated against **end-to-end** latency, and model-only latency is recorded alongside it.
