"""
Trade dataclass -- one completed round-trip trade. One row per Trade
in the ``<BACKTEST_NAME>_trades.csv`` output.

Groupings:

  * ``trigger_*``    -- values on the candle that FIRED the entry
                        signal (bar N in the engine).
  * ``entry_*``      -- fill of the position at bar N+1's open.
  * ``stop_level``   -- computed at signal time, held fixed for the
                        life of the trade.
  * ``exit_*``       -- how the trade closed (target / stop / eod).
  * ``pnl*``, ``bars_held`` -- the result.
  * ``scan_*``       -- the row of the scanner CSV that seeded this
                        (symbol, session_date) candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd


@dataclass
class Trade:
    backtest_name: str
    symbol:        str
    session_date:  date

    # Trigger candle (bar N -- the ema9-crossover-up bar).
    trigger_ts:      pd.Timestamp
    trigger_open:    float
    trigger_high:    float
    trigger_low:     float
    trigger_close:   float
    trigger_ema9:    float
    trigger_vwap:    float
    trigger_relatr:  float
    trigger_rvol:    Optional[float]
    max_recent_relatr: float
    lookback_low:      float

    # Fill (bar N+1 open).
    entry_ts:    pd.Timestamp
    entry_price: float

    # Stop level -- fixed at entry, held for the whole trade.
    stop_level:  float

    # Exit.
    exit_ts:     pd.Timestamp
    exit_price:  float
    exit_reason: str            # "target" | "stop" | "eod"

    # Result.
    pnl:         float          # exit_price - entry_price
    pnl_pct:     float          # pnl / entry_price
    bars_held:   int            # number of bars from entry to exit (>= 1)

    # Scanner-CSV context.
    scan_trigger_ts:     pd.Timestamp
    scan_trigger_relatr: float
    scan_trigger_rvol:   float
    # Day-level premarket % change carried from the scan CSV.
    # ``None`` / NaN when the scan had no premarket data that session.
    scan_premarket_change_pct: Optional[float] = None
