"""Timestamp parsing for CIC-IDS2017 TrafficLabelling files (ADR-0003).

Source format is ``d/m/yyyy h:mm`` (sometimes with ``:ss`` and/or ``AM``/``PM``). Afternoon
files use a 12-hour clock *without* a meridiem marker. The captures ran 08:00-18:00, so a bare
hour in 1..7 is interpreted as PM.
"""

from __future__ import annotations

import pandas as pd

AFTERNOON_HOUR_CUTOFF = 8  # bare hours below this are afternoon (13:00-19:59)

_PATTERN = (
    r"^\s*(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})\s+"
    r"(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?\s*(?P<ampm>[AaPp][Mm])?\s*$"
)


def parse_timestamps(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse raw timestamp strings.

    Returns:
        ``(timestamps, pm_fixed)``: ``datetime64[ns]`` series (``NaT`` where unparseable) and
        a boolean series marking rows whose bare 12-hour clock was shifted to PM.
    """
    parts = values.astype("string").str.extract(_PATTERN)
    hour = pd.to_numeric(parts["H"], errors="coerce")
    ampm = parts["ampm"].str.upper()

    # Comparisons against missing values yield <NA>; force every mask to plain bool so that
    # ``where`` never treats an unknown condition as "replace".
    is_pm = ampm.eq("PM").fillna(False).astype(bool)
    is_am = ampm.eq("AM").fillna(False).astype(bool)
    no_marker = ampm.isna().astype(bool)
    explicit_pm = is_pm & hour.lt(12).fillna(False).astype(bool)
    explicit_am_midnight = is_am & hour.eq(12).fillna(False).astype(bool)
    bare_afternoon = no_marker & hour.between(1, AFTERNOON_HOUR_CUTOFF - 1).fillna(False).astype(
        bool
    )

    hour = hour.where(~(explicit_pm | bare_afternoon), hour + 12)
    hour = hour.where(~explicit_am_midnight, 0)

    frame = pd.DataFrame(
        {
            "year": pd.to_numeric(parts["y"], errors="coerce"),
            "month": pd.to_numeric(parts["m"], errors="coerce"),
            "day": pd.to_numeric(parts["d"], errors="coerce"),
            "hour": hour,
            "minute": pd.to_numeric(parts["M"], errors="coerce"),
            "second": pd.to_numeric(parts["S"], errors="coerce").fillna(0),
        }
    )
    valid = frame[["year", "month", "day", "hour", "minute"]].notna().all(axis=1)
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    if valid.any():
        parsed.loc[valid] = pd.to_datetime(frame.loc[valid], errors="coerce")
    return parsed, bare_afternoon & valid
