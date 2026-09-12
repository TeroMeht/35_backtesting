"""
Load a trades CSV produced by ``python -m backtester`` into a plain
list of ``TradeRow`` records. This module reads ONLY the columns the
position manager needs -- entry / stop / exit + identifiers -- and
stays independent of the backtester's Trade dataclass. That way a
trades CSV from any source with the same column shape works here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


logger = logging.getLogger(__name__)


REQUIRED_COLS = [
    "backtest_name", "symbol", "session_date",
    "entry_ts", "entry_price", "stop_level",
    "exit_ts", "exit_price", "exit_reason",
    "pnl", "pnl_pct", "bars_held",
]


@dataclass(frozen=True)
class TradeRow:
    """Immutable slice of one trades-CSV row -- exactly what the
    position manager needs to size and account for the trade."""
    backtest_name: str
    symbol:        str
    session_date:  date

    entry_ts:      pd.Timestamp
    entry_price:   float
    stop_level:    float

    exit_ts:       pd.Timestamp
    exit_price:    float
    exit_reason:   str

    # Per-share pnl and % return the backtester recorded on this
    # trade -- kept as reference for audits (this module recomputes
    # dollar pnl from sized shares).
    pnl_per_share: float
    pnl_pct:       float
    bars_held:     int


def load_trades_csv(path) -> list[TradeRow]:
    """Read the trades CSV, coerce types, and return the rows sorted
    by ``(entry_ts, symbol)`` so the event loop sees them in
    chronological entry order."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"trades CSV not found: {p} -- "
            f"run `python -m backtester` first",
        )
    df = pd.read_csv(p)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{p}: trades CSV missing columns {missing}")

    df["session_date"] = pd.to_datetime(df["session_date"]).dt.date

    # Both timestamp columns may carry mixed DST offsets across a
    # scan window; parse via UTC then convert to Helsinki so the
    # resulting column is homogeneous across a DST boundary.
    for c in ("entry_ts", "exit_ts"):
        ts = pd.to_datetime(df[c], utc=True, errors="raise")
        df[c] = ts.dt.tz_convert("Europe/Helsinki")

    df = df.sort_values(["entry_ts", "symbol"]).reset_index(drop=True)

    rows: list[TradeRow] = []
    for r in df.itertuples(index=False):
        rows.append(TradeRow(
            backtest_name = str(r.backtest_name),
            symbol        = str(r.symbol),
            session_date  = r.session_date,
            entry_ts      = pd.Timestamp(r.entry_ts),
            entry_price   = float(r.entry_price),
            stop_level    = float(r.stop_level),
            exit_ts       = pd.Timestamp(r.exit_ts),
            exit_price    = float(r.exit_price),
            exit_reason   = str(r.exit_reason),
            pnl_per_share = float(r.pnl),
            pnl_pct       = float(r.pnl_pct),
            bars_held     = int(r.bars_held),
        ))
    logger.info("loaded %d trade rows from %s", len(rows), p)
    return rows
