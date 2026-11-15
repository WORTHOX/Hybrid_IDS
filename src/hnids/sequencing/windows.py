"""Boundary-respecting sliding windows (FR-6).

Rows must be sorted so that each *group* (capture/block, or a serving session) occupies one
contiguous run, chronologically ordered inside. A window of length ``L`` ending at row ``e`` is
``rows[e-L+1 : e+1]`` and is produced only when all ``L`` rows share ``e``'s group. Its label
is the label of the final flow. The first ``L-1`` rows of each group act only as context
("warm-up"), and groups shorter than ``L`` produce no windows.

Windows are represented by their end indices so that the full ``(N, L, F)`` tensor is never
materialized.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hnids.errors import DataError, ErrorCode


@dataclass(frozen=True)
class WindowStats:
    eligible_rows: int
    windows: int
    groups: int
    boundary_resets: int
    warmup_rows: int
    short_groups: int


@dataclass(frozen=True)
class WindowIndex:
    ends: np.ndarray  # int64 positions of each window's final row
    length: int
    stats: WindowStats


def build_window_index(group_codes: np.ndarray, length: int) -> WindowIndex:
    """Compute window end positions for contiguous, sorted groups.

    Args:
        group_codes: One integer group code per row. Equal codes must be contiguous.
        length: Window length ``L`` (>= 2).

    Raises:
        DataError: when a group's rows are not contiguous (which would let windows cross
            boundaries) or when ``length`` is invalid.
    """
    if length < 2:
        raise DataError("window length must be >= 2", code=ErrorCode.INPUT_INVALID)
    codes = np.asarray(group_codes)
    n = codes.shape[0]
    if n == 0:
        stats = WindowStats(0, 0, 0, 0, 0, 0)
        return WindowIndex(ends=np.empty(0, dtype=np.int64), length=length, stats=stats)

    change = np.empty(n, dtype=bool)
    change[0] = True
    change[1:] = codes[1:] != codes[:-1]
    run_starts = np.flatnonzero(change)
    run_codes = codes[run_starts]
    if np.unique(run_codes).shape[0] != run_codes.shape[0]:
        raise DataError(
            "group rows are not contiguous; sort by group before windowing",
            code=ErrorCode.SPLIT_INVALID,
        )
    run_lengths = np.diff(np.append(run_starts, n))
    position = np.arange(n) - np.repeat(run_starts, run_lengths)
    ends = np.flatnonzero(position >= length - 1).astype(np.int64)

    stats = WindowStats(
        eligible_rows=int(n),
        windows=int(ends.shape[0]),
        groups=int(run_starts.shape[0]),
        boundary_resets=int(run_starts.shape[0] - 1),
        warmup_rows=int(np.minimum(run_lengths, length - 1).sum()),
        short_groups=int((run_lengths < length).sum()),
    )
    return WindowIndex(ends=ends, length=length, stats=stats)


def gather_windows(features: np.ndarray, ends: np.ndarray, length: int) -> np.ndarray:
    """Materialize ``(len(ends), length, F)`` windows for a batch of end indices."""
    offsets = np.arange(-length + 1, 1, dtype=np.int64)
    return features[ends[:, None] + offsets[None, :]]
