"""
Thin DuckDB read helpers. The scanner needs:

  * daily bars for a lookback ending before the scan window (for
    ATR + SMA200 + prev_close prep, per symbol per session).
  * intraday bars for the baseline-lookback + scan window (per
    symbol per session, streamed through SymbolSessionState).
  * the universe (either from universe_snapshot, or CSV fallback).

Everything is one connection, opened read-only. Timestamps come back
tz-aware in Europe/Helsinki so downstream slot-key derivation and
session_date grouping match how 34_dataengine wrote the rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional

import duckdb
import pandas as pd

from ._config import settings


logger = logging.getLogger(__name__)


# Same map 34_dataengine uses. Duplicated here (5 rows) instead of
# importing 34 -- keeps 35 independent of a sibling project's layout.
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
    """
    Open a read-only connection to the backfill cache and set its
    session timezone so intraday ts values render in Europe/Helsinki
    when converted to pandas (DuckDB stores TIMESTAMPTZ as UTC
    internally regardless of the write tag; SET TIMEZONE controls
    what tz-aware timestamps come back as).
    """
    from pathlib import Path
    path = db_path or settings.BACKFILL_DB_PATH
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"DuckDB cache not found at {p} -- "
            f"run `python -m backfiller` in 34_dataengine first",
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
    """
    Daily bars for `symbols` in [start, end]. Columns:
    symbol, session_date, open, high, low, close, volume.
    """
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
    # DuckDB returns DATE columns as datetime64[us]; pandas won't
    # compare those against Python `date` scalars. Normalize once so
    # every downstream ``session_date`` comparison is date-vs-date.
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
    """
    Intraday bars for `symbols` whose Helsinki-local calendar date
    falls in [start, end]. Returns tz-aware `ts` (Europe/Helsinki),
    plus a derived `session_date` (local date) so grouping stays
    stable across DST boundaries. Columns:
    symbol, ts, session_date, open, high, low, close, volume.
    """
    syms = list(symbols)
    table = _table_for(bar_size)
    if not syms:
        return pd.DataFrame(
            columns=["symbol", "ts", "session_date",
                     "open", "high", "low", "close", "volume"],
        )
    # ts stored as UTC instants; SET TIMEZONE above makes reads return
    # them in Europe/Helsinki. We still filter by a UTC instant range
    # padded by a day on each side to catch bars whose local date
    # equals `start`/`end` but whose UTC instant lives on an adjacent
    # calendar day (belt-and-braces; the final in-python filter on
    # session_date is what actually decides membership).
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
    # Force Helsinki tz -- DuckDB returns UTC unless SET TIMEZONE took
    # effect on this connection AND the driver honored it, which
    # varies by driver version. Convert explicitly to be safe.
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize("UTC")
    df["ts"] = df["ts"].dt.tz_convert(settings.TIMEZONE)
    df["session_date"] = df["ts"].dt.date
    df = df[(df["session_date"] >= start) & (df["session_date"] <= end)]
    return df.reset_index(drop=True)


def load_universe(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """
    Read the tradable universe from ``universe_snapshot`` in the
    DuckDB cache. Uses the newest ``snapshot_date`` present in the
    table. Raises if the table is empty -- re-run 34_dataengine's
    backfiller with ``RECORD_UNIVERSE_SNAPSHOT = True`` first.
    """
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM universe_snapshot",
    ).fetchone()
    snap = row[0] if row else None
    if snap is None:
        raise RuntimeError(
            "universe_snapshot is empty -- re-run 34_dataengine's "
            "backfiller with RECORD_UNIVERSE_SNAPSHOT = True first.",
        )
    rows = conn.execute(
        "SELECT symbol FROM universe_snapshot "
        " WHERE snapshot_date = ? "
        " ORDER BY symbol",
        [snap],
    ).fetchall()
    return [r[0] for r in rows]
