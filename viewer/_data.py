"""
Thin DuckDB read helpers -- own copy, independent of scanner and
backtester. Same schema, same timestamp conventions
(intraday ``ts`` returned as Europe/Helsinki tz-aware; ``session_date``
normalized to python ``date`` objects).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import duckdb
import pandas as pd

from ._config import settings


logger = logging.getLogger(__name__)


_BAR_SIZE_TO_TABLE: dict[str, str] = {
    "1d":  "bar_daily",
    "1m":  "bar_intraday_1m",
    "2m":  "bar_intraday_2m",
    "5m":  "bar_intraday_5m",
    "30m": "bar_intraday_30m",
}
_ALIASES: dict[str, str] = {
    "1min": "1m", "2min": "2m", "5min": "5m", "30min": "30m",
    "1day": "1d", "d": "1d", "day": "1d", "daily": "1d",
}


def _table_for(bar_size: str) -> str:
    key = _ALIASES.get(bar_size, bar_size)
    try:
        return _BAR_SIZE_TO_TABLE[key]
    except KeyError as e:
        raise ValueError(
            f"unknown bar_size {bar_size!r} -- valid: "
            f"{sorted(_BAR_SIZE_TO_TABLE)} (aliases: {sorted(_ALIASES)})",
        ) from e


def open_conn(db_path: Optional[str] = None) -> duckdb.DuckDBPyConnection:
    path = db_path or settings.BACKFILL_DB_PATH
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"DuckDB cache not found at {p} -- "
            f"run 34_dataengine's backfiller first.",
        )
    conn = duckdb.connect(str(p), read_only=True)
    conn.execute(f"SET TIMEZONE = '{settings.TIMEZONE}'")
    return conn


def read_daily(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: Iterable[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    syms = list(symbols)
    if not syms:
        return pd.DataFrame(
            columns=["symbol", "session_date",
                     "open", "high", "low", "close", "volume"],
        )
    q = f"""
        SELECT symbol, session_date, open, high, low, close, volume
          FROM {_table_for('1d')}
         WHERE symbol = ANY(?)
           AND session_date BETWEEN ? AND ?
         ORDER BY symbol, session_date
    """
    df = conn.execute(q, [syms, start, end]).df()
    if not df.empty:
        df["session_date"] = df["session_date"].dt.date
    return df


def read_intraday(
    conn: duckdb.DuckDBPyConnection,
    *,
    bar_size: str,
    symbols: Iterable[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    syms = list(symbols)
    table = _table_for(bar_size)
    if not syms:
        return pd.DataFrame(
            columns=["symbol", "ts", "session_date",
                     "open", "high", "low", "close", "volume"],
        )
    start_ts = datetime.combine(start - timedelta(days=1), time.min, tzinfo=timezone.utc)
    end_ts   = datetime.combine(end   + timedelta(days=2), time.min, tzinfo=timezone.utc)
    q = f"""
        SELECT symbol, ts, open, high, low, close, volume
          FROM {table}
         WHERE symbol = ANY(?)
           AND ts >= ? AND ts < ?
         ORDER BY symbol, ts
    """
    df = conn.execute(q, [syms, start_ts, end_ts]).df()
    if df.empty:
        df["session_date"] = pd.Series(dtype="object")
        return df[["symbol", "ts", "session_date",
                   "open", "high", "low", "close", "volume"]]
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize("UTC")
    df["ts"] = df["ts"].dt.tz_convert(settings.TIMEZONE)
    df["session_date"] = df["ts"].dt.date
    df = df[(df["session_date"] >= start) & (df["session_date"] <= end)]
    return df.reset_index(drop=True)
