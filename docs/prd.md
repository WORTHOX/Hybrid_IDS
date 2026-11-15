# Product Requirements Document: Hybrid Network Intrusion Detection System

**Status:** Draft 1.0  
**Date:** 10 November 2026  
**Product owner:** TBD  
**Engineering owner:** TBD  
**Security owner:** TBD

## 1. Product summary

Build a production-ready network intrusion detection system (NIDS) that classifies network-flow sequences as benign or malicious and identifies the attack class. The system combines:

- an XGBoost classifier that generates out-of-fold class probabilities;
- a Bidirectional GRU and Transformer encoder that model temporal flow patterns; and
- an optional, tightly controlled Weight-Space Programming (WSP) mechanism for post-training sensitivity experiments on rare attack classes.

The first release is an offline, benchmark-validated detection service and inference library. It will ingest flow records, produce class probabilities and audit-ready detection events, and integrate with downstream SOC/SIEM workflows. It will not automatically block traffic or autonomously modify production model weights.

## 2. Problem statement

SOC teams need accurate detection across both common and rare attacks without introducing unacceptable false-positive volume. Existing academic IDS prototypes often overstate performance because they leak information across time or dataset partitions, and they frequently lack the security, observability, reproducibility, and resource controls required for production operation.

The product must demonstrate that its results are reproducible, leakage-resistant, safe to deploy, and operationally useful. Accuracy alone is insufficient: the system must preserve capture boundaries, survive malformed telemetry, load artifacts safely, expose confidence and model-version context, and meet a defined latency and reliability envelope.

## 3. Goals and success criteria

### 3.1 Goals

1. Detect and classify network intrusions from flow-level telemetry with strong performance across imbalanced classes.
2. Model temporal behavior without creating artificial sequences across capture or sensor-session boundaries.
3. Prevent train/validation/test contamination in scaling, feature generation, sequence construction, and tuning.
4. Provide a secure, reproducible training and inference pipeline suitable for CI/CD and SOC integration.
5. Produce explainable operational outputs: predicted class, calibrated confidence, model version, event time, and relevant processing metadata.
6. Allow controlled evaluation of rare-class sensitivity adjustments without retraining, while preserving rollback and approval controls.

### 3.2 Launch success metrics

All thresholds apply to a frozen, independently evaluated test set unless explicitly stated otherwise.

| Category | Metric | Launch threshold |
|---|---|---|
| Detection quality | Macro F1 | At least 0.95 on CIC-IDS2017 benchmark evaluation |
| Rare-class quality | Per-class recall and precision | Reported for every class; minimum thresholds set before model sign-off |
| Operational noise | False-positive rate on benign traffic | Threshold agreed with SOC before pilot; measured per capture and per hour |
| Calibration | Expected calibration error or Brier score | Baseline recorded and no regression beyond approved tolerance |
| Performance | Mean single-window inference latency | At most 5 ms on the named production hardware and runtime |
| Reliability | Valid requests producing a finite prediction | At least 99.9% during load testing |
| Security | Critical/high findings in release scan | Zero unresolved findings |
| Data integrity | Cross-boundary sequences or test-data leakage | Zero detected |
| Reproducibility | Repeat training with fixed seed/config | Metrics within an agreed tolerance and artifacts traceable to inputs |

The 0.95 macro-F1 and 5 ms targets originate from the implementation guide and remain provisional until the deployment hardware, label taxonomy, split protocol, and SOC cost model are approved.

## 4. Users and stakeholders

| User or stakeholder | Need |
|---|---|
| SOC analyst | Prioritized, trustworthy alerts with class, confidence, time, and model context |
| Detection engineer | Configurable thresholds, class-level metrics, replay testing, and false-positive analysis |
| ML engineer | Reproducible data preparation, training, evaluation, artifact packaging, and rollback |
| Platform/SRE team | Bounded resource use, health telemetry, deployment automation, and failure isolation |
| Security/compliance reviewer | Safe artifact handling, audit logs, provenance, access control, and release evidence |
| Product owner | Clear pilot outcomes, operational value, and known limitations |

## 5. Scope

### 5.1 In scope for MVP

- CIC-IDS2017 ingestion and a documented adapter contract for equivalent flow telemetry.
- Defensive schema validation, column normalization, finite-value sanitation, bounded-memory ingestion, and quarantine of rejected records.
- Capture-aware chronological partitioning and temporal sequence construction.
- Training-only feature scaling and out-of-fold XGBoost probability generation.
- Hybrid BiGRU/Transformer multiclass model training and evaluation.
- Class imbalance handling, mixed-precision training where supported, checkpointing, and early stopping.
- Batch and single-window inference through a Python API or service interface.
- Detection output suitable for SIEM ingestion.
- Structured logs, metrics, model/data provenance, health checks, and release evidence.
- Offline, approval-gated WSP experiments with before/after evaluation and rollback.

### 5.2 Out of scope for MVP

- Automatic traffic blocking, firewall rule changes, or incident remediation.
- Packet capture, deep packet inspection, or payload analysis.
- Autonomous online learning or autonomous WSP changes in production.
- Claims of generalization to unseen enterprise networks without pilot data.
- Federated learning, multi-tenant model personalization, or adversarial retraining.
- Full analyst case-management UI.

## 6. User journeys

### 6.1 Model development and release

1. An ML engineer registers approved raw captures and a versioned configuration.
2. The pipeline validates paths and schemas, streams and sanitizes records, and records rejected-row counts.
3. It creates chronological, capture-aware train/validation/test partitions.
4. It fits preprocessing and XGBoost components only on training data, builds bounded sequences, and trains the neural model.
5. It evaluates the frozen candidate, produces class-level metrics and security/quality evidence, and packages signed or checksummed artifacts.
6. An authorized reviewer approves or rejects promotion.

### 6.2 SOC inference

1. A flow exporter or upstream adapter sends ordered flow telemetry with capture/session identity and event time.
2. The service validates and transforms the record, updates the correct sequence buffer, and makes a prediction once a full window is available.
3. The service emits a detection event containing the predicted label, class probabilities, confidence, model version, event time, capture/session identity, and validation status.
4. Invalid records are rejected or quarantined with a reason code; they do not corrupt active sequence buffers.

### 6.3 Sensitivity experiment

1. A detection engineer selects a target minority class and defines the intended precision/recall trade-off.
2. WSP changes are applied to a copy of an approved model artifact in an offline environment.
3. The full frozen evaluation suite runs, including global regression and calibration checks.
4. The experiment and parameter delta are logged. Production promotion requires the same approval and rollback controls as a newly trained model.

## 7. Functional requirements

### FR-1: Configuration and reproducibility

- Configuration must be immutable during a run and validated before data or model operations begin.
- A run must record the code version, configuration, random seeds, dependency/runtime versions, dataset manifest, split manifest, feature schema, and artifact hashes.
- Randomness must be seeded across Python, NumPy, PyTorch, XGBoost, and applicable accelerator libraries.
- A configuration or schema mismatch must fail fast with a structured error.

### FR-2: Secure data and artifact access

- All data and artifact paths must resolve inside explicitly authorized roots after canonicalization and symlink resolution.
- The system must reject traversal attempts and unauthorized paths before opening a handle.
- Model loading must not use unrestricted pickle deserialization. PyTorch weights must use a safe state-dictionary path with `weights_only=True` where supported.
- Non-tensor preprocessing metadata must use a non-executable structured format or a cryptographically verified alternative.
- Every promoted artifact must have integrity metadata and provenance.

### FR-3: Defensive ingestion

- Ingestion must operate in bounded chunks and must not require loading the full source dataset into memory.
- It must normalize feature names deterministically and attach a stable `capture_id` before concatenation.
- It must preserve or derive an event timestamp sufficient for chronological ordering.
- It must detect duplicates, invalid types, missing required fields, non-finite values, and schema drift.
- Sanitization behavior must be explicit per feature. Unsalvageable records must be quarantined with reason codes and counters.
- Numeric representations should use memory-efficient types where they do not change defined behavior.

### FR-4: Leakage-resistant partitioning and preprocessing

- Rows must be ordered chronologically within their capture/session.
- Train, validation, and test membership must be stored in a replayable split manifest.
- No temporal sequence may span split or capture/session boundaries.
- Scalers, imputers, feature selectors, encoders, and similar transformations must fit on training data only.
- The test set must remain untouched until final candidate evaluation.
- The precise split strategy must be documented. A random row split is prohibited.

### FR-5: XGBoost probability features

- Training rows must receive probabilities from a model that did not train on those rows.
- Cross-validation folds must preserve the temporal/group constraints defined by the approved evaluation protocol; plain shuffled stratified folds are not acceptable if they mix correlated captures.
- A final XGBoost model may be fit on the complete training partition to generate validation, test, and production features.
- Output probability vectors must have one finite value per class and sum to one within numerical tolerance.

### FR-6: Temporal sequence construction

- The default sequence length is 20 flows and must be configurable.
- Sequence buffers must be keyed by capture/session identity and reset at capture, split, schema, and explicit session boundaries.
- A sequence label must correspond to its final flow.
- The pipeline must expose counts of eligible rows, produced sequences, boundary resets, and discarded warm-up rows.

### FR-7: Hybrid model

- The baseline architecture must support a two-layer bidirectional GRU, positional encoding, a two-layer multi-head Transformer encoder, and a multiclass classification head.
- Default architecture values from the source guide are: GRU hidden size 128, Transformer dimension 256, four attention heads, feed-forward dimension 512, and dropout 0.2.
- The forward interface must accept `(batch, sequence_length, fused_feature_count)` and return finite logits shaped `(batch, class_count)`.
- Architecture and class taxonomy must be embedded in or linked to model metadata.

### FR-8: Training and candidate selection

- Training must support class-weighted cross-entropy, AdamW, learning-rate reduction, accelerator-aware mixed precision, and early stopping.
- Candidate selection must use validation macro F1, with class-level recall, precision, confusion, and calibration visible to reviewers.
- Checkpoints must be written only to an authorized artifact root and must be reload-tested before promotion.
- Training interruption must preserve sufficient state to diagnose the failure; resumability is desirable but not required for MVP.

### FR-9: Inference and detection events

- Inference must use the exact versioned preprocessing, class mapping, XGBoost model, and neural weights approved together.
- The service must support single-window and bounded batch prediction.
- A successful response/event must include:
  - event and processing timestamps;
  - predicted class and class probabilities;
  - confidence and configured decision threshold;
  - model bundle version and feature-schema version;
  - capture/session identifier or privacy-preserving correlation key;
  - validation/sanitization status;
  - a unique detection identifier.
- Invalid inputs must return a stable error code and must not produce a normal detection.
- Low-confidence results must be distinguishable from high-confidence alerts.

### FR-10: WSP governance

- WSP must be disabled in the production runtime by default.
- Changes must be applied only to a copied artifact, never in place.
- Every change must record the base model hash, target class, exact tensor delta or transformation, author, reason, time, and evaluation result.
- Promotion requires full regression evaluation, security approval, versioning, and a one-step rollback to the unmodified model.
- The product must not claim that WSP avoids catastrophic forgetting or improves rare-class performance unless the frozen evaluation demonstrates it.

### FR-11: Observability and audit

- Production code must use structured logs and must not rely on `print` output.
- Metrics must include request rate, latency percentiles, input rejection/quarantine rate, missing/non-finite value rate, class distribution, confidence distribution, error rate, and resource utilization.
- Alerts must cover artifact-integrity failure, schema mismatch, sustained error or quarantine spikes, latency breach, resource exhaustion, and major distribution drift.
- Logs must not expose raw sensitive flow fields unless explicitly approved and access-controlled.

## 8. Non-functional requirements

### Security

- Least-privilege access must apply to datasets, artifacts, configuration, and deployment actions.
- CI must run dependency, static application security, and secret scans.
- No release may contain unresolved critical or high-severity findings without a documented exception approved by security.
- Artifact promotion and WSP changes must be attributable to an authorized identity.

### Performance and capacity

- The inference service must meet the 5 ms mean target on the named reference environment; p95 and p99 targets must be defined before pilot.
- Load tests must define sustainable flows per second, maximum batch size, memory ceiling, and behavior under backpressure.
- Ingestion and inference must fail safely under malformed or oversized input rather than exhausting host resources.

### Availability and recovery

- Target service-level objectives, deployment topology, and recovery-time/recovery-point objectives must be approved before production launch.
- A failed model load must leave the last known-good model serving or mark the instance unhealthy; it must not serve partially initialized predictions.
- Rollback to the previous model bundle must be tested as part of release validation.

### Maintainability

- The codebase must maintain clear modules for configuration, security, ingestion, preprocessing, sequence construction, models, training, evaluation, and serving.
- Public interfaces must be typed. CI must enforce formatting, linting, tests, and strict type checks for production modules.
- Domain errors must be explicit and machine-readable.

### Privacy and compliance

- Data retention, permitted fields, pseudonymization, regional handling, and analyst access must follow the organization's network-telemetry policy.
- The MVP must document whether source/destination identifiers are retained in model inputs, outputs, logs, and quarantine storage.

## 9. Evaluation and release gates

A model bundle may enter a production pilot only when all applicable gates pass:

1. Dataset and label taxonomy are versioned and approved.
2. Split and leakage audit shows no training influence on validation/test transformations or labels.
3. Unit tests confirm path containment, safe loading, sanitation, sequence boundaries, output shapes, finite outputs, and artifact compatibility.
4. Integration tests cover raw input through detection event and invalid-input quarantine.
5. Macro and per-class metrics, false-positive rate, calibration, and confusion matrix are reviewed.
6. Latency, throughput, memory, backpressure, and failure recovery are measured on the reference deployment.
7. Static typing, linting, formatting, dependency, and security scans pass.
8. Artifact integrity and rollback are verified.
9. SOC completes a replay-based acceptance exercise and approves alert usefulness.
10. Security, ML, platform, and product owners sign off on the release evidence.

## 10. Acceptance criteria for MVP

- Given approved CIC-IDS2017 source files, the pipeline completes using bounded memory and emits a dataset-quality report.
- Re-running with the same inputs, code, configuration, and environment produces the same split manifest and metrics within the approved deterministic tolerance.
- Automated tests prove no sequence crosses a capture or split boundary.
- Automated tests prove validation and test records do not influence fitted preprocessing or training OOF predictions.
- Malformed rows, NaN/Inf values, missing fields, path traversal attempts, incompatible schemas, and tampered artifacts are rejected or quarantined as specified.
- The packaged model reloads through the production inference path and returns finite, normalized class probabilities.
- The benchmark and operational gates in Section 3 are met or a deviation is explicitly approved and recorded.
- A detection event can be consumed by the selected SIEM integration without manual transformation.
- A model rollback completes successfully in a staging exercise.
- Any WSP candidate is independently versioned, fully evaluated, auditable, and reversible.

## 11. Delivery phases

### Phase 1: Foundations

Finalize the label taxonomy, telemetry schema, split protocol, reference hardware, configuration model, authorized storage roots, and threat model.

### Phase 2: Data pipeline

Deliver bounded ingestion, sanitation/quarantine, provenance, capture-aware splitting, preprocessing, sequence construction, and leakage tests.

### Phase 3: Modeling

Deliver grouped/temporal OOF XGBoost features, hybrid model training, evaluation, calibration analysis, and reproducible artifact packaging.

### Phase 4: Serving and operations

Deliver inference interface, SIEM event schema, observability, integrity checks, load testing, deployment automation, and rollback.

### Phase 5: Pilot and hardening

Run offline enterprise-traffic replay, tune thresholds using SOC feedback, document drift baselines, and complete production readiness review.

### Phase 6: WSP research track

Evaluate whether controlled WSP changes produce a durable, acceptable precision/recall trade-off. This track does not block the core MVP and does not enter production without separate approval.

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| CIC-IDS2017 does not represent current enterprise traffic | Benchmark success may not transfer to production | Require enterprise replay and shadow-mode pilot before operational claims |
| Temporal or group leakage inflates results | Invalid launch decision | Store manifests, use capture-aware folds, add automated leakage assertions |
| Rare-class scarcity creates unstable metrics | Misleading sensitivity claims | Report support and confidence intervals; define minimum sample requirements |
| Class weighting or WSP increases false positives | Analyst overload | Use threshold/cost analysis, approval gates, shadow evaluation, and rollback |
| Schema drift corrupts predictions | Silent loss of quality | Version schemas, validate inputs, quarantine mismatches, and alert on drift |
| Artifact tampering or unsafe loading | Code execution or corrupted predictions | Non-executable formats, integrity checks, authorized roots, least privilege |
| Sequence state is mixed across sensors/sessions | Incorrect temporal context | Key and bound buffers, reset on explicit boundaries, test concurrency behavior |
| 5 ms target excludes preprocessing/XGBoost/network overhead | Misstated end-to-end performance | Define and measure both model-only and end-to-end latency |
| Deterministic GPU settings reduce throughput | Capacity shortfall | Benchmark the approved reproducibility/performance mode explicitly |

## 13. Dependencies

- Approved access to CIC-IDS2017 and representative enterprise replay data.
- Confirmed flow-export format, stable capture/session identifier, event timestamp, and label mapping.
- Reference CPU/GPU hardware and serving runtime.
- Artifact registry or protected model storage with integrity and access controls.
- CI/CD environment capable of type, test, security, and performance checks.
- SIEM ingestion contract and SOC reviewers for acceptance testing.
- Organizational decisions for retention, privacy, and model-change approval.

## 14. Open decisions

1. What exact class taxonomy should production use, and which classes may be merged due to low support?
2. What capture-aware split and cross-validation strategy is authoritative for benchmark reporting?
3. What false-positive cost and per-class minimum recall/precision define an acceptable SOC trade-off?
4. What does the 5 ms latency target include, and what are the p95/p99 targets and required throughput?
5. Which service protocol and SIEM event schema will be used?
6. How are capture/session boundaries supplied in live traffic, and what is the buffer timeout policy?
7. What confidence calibration method and low-confidence handling policy are required?
8. What data fields may be stored in logs, quarantine, and detection events, and for how long?
9. Who can approve model promotion, threshold changes, and WSP experiments?
10. What drift thresholds trigger investigation, retraining, rollback, or service degradation?

## 15. Assumptions

- The initial deliverable is a detection and classification product, not an inline prevention system.
- CIC-IDS2017 is the initial benchmark, but production readiness requires representative enterprise replay data.
- Upstream telemetry provides a trustworthy event time and a capture/session boundary key.
- The default 70/15/15 chronological partition, 20-flow windows, five OOF folds, and architecture hyperparameters are starting points, not immutable product requirements.
- WSP is experimental and approval-gated; no autonomous production weight editing is permitted.

