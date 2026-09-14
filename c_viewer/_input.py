"""
Load the trades CSV produced by ``python -m backtester`` -- one row
per completed round-trip trade. Types are coerced so the viewer
can index and sort by (symbol, session_date, entry_ts) without
surprises.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd


logger = logging.getLogger(__name__)


REQUIRED_COLS = [
    "backtest_name", "symbol", "session_date",
    "trigger_ts",
    "trigger_open", "trigger_high", "trigger_low", "trigger_close",
    "trigger_ema9", "trigger_vwap", "trigger_relatr", "scan_trigger_rvol",
    "max_recent_relatr", "lookback_low",
    "entry_ts", "entry_price", "stop_level",
    "exit_ts", "exit_price", "exit_reason",
    "pnl", "pnl_pct", "bars_held",
]


def load_trades_csv(path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"trades CSV not found: {p} -- run `python -m backtester` first",
        )
    df = pd.read_csv(p)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{p}: trades CSV missing columns {missing}")

    df["session_date"] = pd.to_datetime(df["session_date"]).dt.date

    # All three ts columns may carry mixed DST offsets across a scan
    # window; parse via UTC then convert to Helsinki so the resulting
    # column is homogeneous even across a DST boundary.
    for c in ("trigger_ts", "entry_ts", "exit_ts"):
        ts = pd.to_datetime(df[c], utc=True, errors="raise")
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize("Europe/Helsinki")
        else:
            ts = ts.dt.tz_convert("Europe/Helsinki")
        df[c] = ts

    return df.sort_values(["session_date", "symbol", "entry_ts"]).reset_index(drop=True)
