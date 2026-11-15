# ADR-0003: Telemetry schema and source files

- Status: Accepted
- Date: 2026-11-11

## Context
FR-3 and FR-4 require event timestamps and capture identity for chronological, boundary-aware sequences. The common Kaggle copy `chethuhn/network-intrusion-dataset` is the *MachineLearningCVE* variant. It has 79 columns and no `Timestamp`, `Flow ID` or IP columns, so it cannot support those requirements.

## Decision
- **Source:** the Kaggle dataset `pshikk/cicids2017-untampered`, folder `GeneratedLabelledFlows/TrafficLabelling /`. The folder name has a trailing space. It holds 8 day-files with 85 columns. The original data is CIC-IDS2017 from the Canadian Institute for Cybersecurity, UNB, and it must be cited in any publication. The Kaggle mirror lists its license as "unknown".
- **Encoding:** cp1252. Labels contain byte `0x96`, an en-dash, as in "Web Attack – XSS".
- **Header normalization:** strip whitespace, lowercase, and replace non-alphanumeric characters with `_`. Drop the duplicated `Fwd Header Length.1` column after checking that it equals `fwd_header_length`.
- **capture_id:** the source file stem, for example `Thursday-WorkingHours-Morning-WebAttacks`.
- **Timestamps:** stored as `d/m/yyyy h:mm`, with minute resolution and no seconds. Afternoon files may use a 12-hour clock without AM/PM, so hours 1–7 are mapped to 13–19. The capture window was 08:00–18:00. Rows are ordered by `(timestamp, source_row_index)`, which keeps ties stable.
- **Model features:** the 77 numeric flow statistics. Identifiers (`flow_id`, `source_ip`, `destination_ip`, `source_port`, `destination_port`) and `timestamp` are excluded from model features to prevent shortcut learning. `protocol` is kept as a categorical/numeric feature.
- **Correlation key:** `sha256(salt + flow_id)[:16]` for events and logs. Raw IPs are never emitted.

## Observed data quality (Thursday morning file)
- 458,968 rows, of which 288,602 are fully empty and dropped (not quarantined; counted).
- `Flow Bytes/s` has 115 `Infinity` values and 20 extra NaN. `Flow Packets/s` has 135 `Infinity` values.
- `-1` sentinels appear in `Flow Duration`, IAT minimums and `Init_Win_bytes_*`. In `Init_Win_bytes_*`, -1 means "not observed" and is legitimate.
- Label counts: BENIGN 168,186; Web Attack Brute Force 1,507; XSS 652; SQL Injection 21.

## Consequences
The pipeline depends on the TrafficLabelling layout. Other flow exporters need an adapter to this canonical schema, and the schema is versioned (`FEATURE_SCHEMA_VERSION`).
