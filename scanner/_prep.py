"""
Per-symbol preparation of the scalars ``SymbolSessionState`` needs at
session boot:

  * ``atr``            -- ATR14 as of the previous session's close.
  * ``sma200``         -- SMA of the trailing 200 daily closes ending
                          on the previous session.
  * ``prev_close``     -- previous session's daily close.
  * ``rvol_baseline``  -- dict[time, avg_volume] built from prior
                          ``lookback_days`` sessions' intraday bars,
                          winsorized per-slot the same way 22/32 do.

Each of these is computed ONCE per (symbol, session_date) up front,
then the scan streams intraday bars through ``apply_bar`` for the day.
No look-ahead: every prep value uses only data known BEFORE the
session opens.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Optional

import pandas as pd

from indicators.atr  import atr_series
from indicators.sma  import sma_series
from indicators.rvol import avg_volume_model


logger = logging.getLogger(__name__)


def prep_daily_scalars(
    daily_df: pd.DataFrame,
    *,
    session_date: date,
    atr_span: int = 14,
    sma_period: int = 200,
) -> Optional[tuple[float, float, float]]:
    """
    Given all daily bars for ONE symbol sorted by session_date, return
    ``(atr, sma200, prev_close)`` computed on rows strictly before
    ``session_date``. Returns ``None`` when there aren't enough prior
    sessions to fill an SMA(period) window -- that symbol/day is
    skipped by the caller.
    """
    if daily_df is None or daily_df.empty:
        return None
    prior = daily_df.loc[daily_df["session_date"] < session_date]
    if len(prior) < sma_period:
        return None
    atr = atr_series(prior["high"], prior["low"], prior["close"], span=atr_span)
    sma = sma_series(prior["close"], sma_period)
    atr_v  = atr.iloc[-1]
    sma_v  = sma.iloc[-1]
    if pd.isna(atr_v) or pd.isna(sma_v):
        return None
    return float(atr_v), float(sma_v), float(prior["close"].iloc[-1])


def build_rvol_baselines(
    intraday_df: pd.DataFrame,
    *,
    scan_start: date,
    lookback_days: int,
    winsor_k: Optional[float] = 3.0,
) -> dict[str, dict[time, float]]:
    """
    Build one ``{time -> avg_volume}`` dict per symbol using intraday
    bars in the ``lookback_days`` business days that end the day
    BEFORE ``scan_start``. One baseline is used across the whole scan
    window -- close enough for a signal that only needs "did this slot
    typically trade N shares" and much cheaper than rebuilding daily.

    ``intraday_df`` MUST already have ``session_date`` (local) and
    ``ts`` in Europe/Helsinki (i.e. the shape ``_data.read_intraday``
    returns). Any rows outside the baseline window are dropped.
    """
    if intraday_df is None or intraday_df.empty:
        return {}

    # Pick the trailing `lookback_days` unique sessions that end
    # before scan_start. We take unique session_dates rather than
    # calendar days so illiquid symbols (with holes) still get the
    # full N sessions of coverage where they exist.
    prior = intraday_df.loc[intraday_df["session_date"] < scan_start]
    if prior.empty:
        return {}
    sessions = sorted(prior["session_date"].unique())[-lookback_days:]
    prior = prior.loc[prior["session_date"].isin(sessions)]
    if prior.empty:
        return {}

    df = prior.copy()
    df["time"] = df["ts"].dt.time
    baseline = avg_volume_model(
        df[["symbol", "time", "volume"]], k=winsor_k,
    )
    out: dict[str, dict[time, float]] = {}
    for sym, grp in baseline.groupby("symbol"):
        out[sym] = dict(zip(grp["time"], grp["avg_volume"].astype(float)))
    return out
