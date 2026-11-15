"""Canonical flow schema (ADR-0003). Versioned: bump on any column change."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

FEATURE_SCHEMA_VERSION = 1

# Identifier columns: used for ordering/correlation, never as model features.
FLOW_ID = "flow_id"
SOURCE_IP = "source_ip"
SOURCE_PORT = "source_port"
DESTINATION_IP = "destination_ip"
DESTINATION_PORT = "destination_port"
TIMESTAMP = "timestamp"
LABEL = "label"

IDENTIFIER_COLUMNS: tuple[str, ...] = (
    FLOW_ID,
    SOURCE_IP,
    SOURCE_PORT,
    DESTINATION_IP,
    DESTINATION_PORT,
    TIMESTAMP,
)

# Numeric flow statistics produced by CICFlowMeter, in source order.
RAW_FEATURE_COLUMNS: tuple[str, ...] = (
    "protocol",
    "flow_duration",
    "total_fwd_packets",
    "total_backward_packets",
    "total_length_of_fwd_packets",
    "total_length_of_bwd_packets",
    "fwd_packet_length_max",
    "fwd_packet_length_min",
    "fwd_packet_length_mean",
    "fwd_packet_length_std",
    "bwd_packet_length_max",
    "bwd_packet_length_min",
    "bwd_packet_length_mean",
    "bwd_packet_length_std",
    "flow_bytes_s",
    "flow_packets_s",
    "flow_iat_mean",
    "flow_iat_std",
    "flow_iat_max",
    "flow_iat_min",
    "fwd_iat_total",
    "fwd_iat_mean",
    "fwd_iat_std",
    "fwd_iat_max",
    "fwd_iat_min",
    "bwd_iat_total",
    "bwd_iat_mean",
    "bwd_iat_std",
    "bwd_iat_max",
    "bwd_iat_min",
    "fwd_psh_flags",
    "bwd_psh_flags",
    "fwd_urg_flags",
    "bwd_urg_flags",
    "fwd_header_length",
    "bwd_header_length",
    "fwd_packets_s",
    "bwd_packets_s",
    "min_packet_length",
    "max_packet_length",
    "packet_length_mean",
    "packet_length_std",
    "packet_length_variance",
    "fin_flag_count",
    "syn_flag_count",
    "rst_flag_count",
    "psh_flag_count",
    "ack_flag_count",
    "urg_flag_count",
    "cwe_flag_count",
    "ece_flag_count",
    "down_up_ratio",
    "average_packet_size",
    "avg_fwd_segment_size",
    "avg_bwd_segment_size",
    "fwd_avg_bytes_bulk",
    "fwd_avg_packets_bulk",
    "fwd_avg_bulk_rate",
    "bwd_avg_bytes_bulk",
    "bwd_avg_packets_bulk",
    "bwd_avg_bulk_rate",
    "subflow_fwd_packets",
    "subflow_fwd_bytes",
    "subflow_bwd_packets",
    "subflow_bwd_bytes",
    "init_win_bytes_forward",
    "init_win_bytes_backward",
    "act_data_pkt_fwd",
    "min_seg_size_forward",
    "active_mean",
    "active_std",
    "active_max",
    "active_min",
    "idle_mean",
    "idle_std",
    "idle_max",
    "idle_min",
)

# Rate features that are legitimately non-finite when flow duration is zero (ADR-0003).
RATE_FEATURES: tuple[str, ...] = ("flow_bytes_s", "flow_packets_s")
# Derived indicator: 1 when any rate feature was non-finite and was zero-filled.
RATE_NONFINITE = "rate_nonfinite"

MODEL_FEATURE_COLUMNS: tuple[str, ...] = (*RAW_FEATURE_COLUMNS, RATE_NONFINITE)

REQUIRED_SOURCE_COLUMNS: tuple[str, ...] = (*IDENTIFIER_COLUMNS, *RAW_FEATURE_COLUMNS, LABEL)

DUPLICATE_SUFFIX = "__dup"

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_column(name: str) -> str:
    """``' Flow Bytes/s'`` -> ``'flow_bytes_s'``. Deterministic, idempotent."""
    return _NON_ALNUM.sub("_", name.strip().lower()).strip("_")


def normalize_columns(names: Sequence[str]) -> list[str]:
    """Normalize names and disambiguate repeats with ``__dup<n>`` (first occurrence keeps name)."""
    seen: Counter[str] = Counter()
    result: list[str] = []
    for raw in names:
        base = normalize_column(raw)
        seen[base] += 1
        result.append(base if seen[base] == 1 else f"{base}{DUPLICATE_SUFFIX}{seen[base] - 1}")
    return result
