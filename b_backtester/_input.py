"""
Load a scanner-produced CSV and row-filter it against the current
``Filters``. Every field the filters read is already in the CSV, so
tightening a threshold is a row scan over that file -- no re-scan of
the DuckDB cache needed.

``Filters`` is duck-typed here: it just needs the attributes
``relatr_min``, ``cum_volume_min``, ``rvol_min``,
``require_above_sma200``, ``intraday_start``, ``intraday_end``,
``premarket_change_pct_min``, ``premarket_change_pct_max`` (the last
two default to ``None`` = don't filter that side).
The dataclass lives at the top of ``backtester/__main__.py``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd


logger = logging.getLogger(__name__)


def _parse_time(v) -> time:
    """CSV round-trip turns a ``datetime.time`` into a "HH:MM:SS"
    string. Parse it back, tolerating a datetime.time already."""
    if isinstance(v, time):
        return v
    s = str(v).strip()
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"cannot parse trigger_time value: {v!r}")


def load_and_filter_scan_csv(path, filters) -> pd.DataFrame:
    """
    Read the scan CSV, coerce dtypes, apply ``filters`` row-wise,
    return the surviving rows sorted by (symbol, session_date). Every
    surviving row is one (symbol, session_date) candidate the
    strategy loop will replay.

    Raises if the file is missing or a required column is absent.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"scan CSV not found: {p} -- run `python -m scanner` first",
        )
    df = pd.read_csv(p)

    required = [
        "scan_name", "symbol", "session_date", "trigger_ts", "trigger_time",
        "low", "close", "vwap", "relatr", "rvol", "cum_volume", "sma200",
        "atr", "prev_close", "premarket_change_pct",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{p}: scan CSV missing columns {missing} -- re-run the "
            f"scanner; premarket_change_pct was added in the current "
            f"schema.",
        )

    # Types
    df["session_date"] = pd.to_datetime(df["session_date"]).dt.date
    # trigger_ts strings arrive with their own tz offsets (e.g.
    # "+0300" during EEST and "+0200" during EET), so a single scan
    # spanning a DST boundary produces MIXED offsets that pandas will
    # not infer a common tz from unless we parse via UTC first.
    ts = pd.to_datetime(df["trigger_ts"], utc=True, errors="raise")
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("Europe/Helsinki")
    else:
        ts = ts.dt.tz_convert("Europe/Helsinki")
    df["trigger_ts"]   = ts
    df["trigger_time"] = df["trigger_time"].map(_parse_time)
    # NaN-safe numeric so the bounds comparisons below propagate NaN
    # (a row with unknown premarket_change_pct fails BOTH ``>= min``
    # and ``<= max``, so any active bound drops it -- see below).
    df["premarket_change_pct"] = pd.to_numeric(
        df["premarket_change_pct"], errors="coerce",
    )

    n_in = len(df)

    keep = (
        (df["relatr"]     >= filters.relatr_min)     &
        (df["cum_volume"] >= filters.cum_volume_min) &
        (df["rvol"]       >= filters.rvol_min)       &
        (df["trigger_time"] >= filters.intraday_start) &
        (df["trigger_time"] <  filters.intraday_end)
    )
    if filters.require_above_sma200:
        keep &= (df["close"] > df["sma200"])

    # Premarket-change bounds. Either bound may be None to skip that
    # side. NaN premarket values fail any active bound (an unknown
    # premarket move cannot be confirmed to fall inside the window).
    pm_min = getattr(filters, "premarket_change_pct_min", None)
    pm_max = getattr(filters, "premarket_change_pct_max", None)
    if pm_min is not None:
        keep &= (df["premarket_change_pct"] >= pm_min)
    if pm_max is not None:
        keep &= (df["premarket_change_pct"] <= pm_max)

    out = df.loc[keep].sort_values(
        ["symbol", "session_date"],
    ).reset_index(drop=True)

    logger.info(
        "loaded scan csv %s: %d rows in, %d rows survived filters",
        p.name, n_in, len(out),
    )
    return out
