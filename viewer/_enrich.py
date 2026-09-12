"""
Enrich each (symbol, session_date) session's intraday bars with the
same ``vwap`` / ``ema9`` / ``relatr`` series the strategy sees --
we stream the day's bars through ``SymbolSessionState.apply_bar``
using ATR + prev_close from prior daily bars, identical to how the
backtester seeds its own runs. That way the lines on the chart line
up with the numbers the entry was fired against.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from indicators.candle_row    import CandleRow
from indicators.atr           import atr_series
from indicators.session_state import SymbolSessionState

from ._config import settings


logger = logging.getLogger(__name__)


def _prep_daily(daily_df: pd.DataFrame, session_date: date, atr_span: int
                ) -> Optional[tuple[float, float]]:
    """Return (atr, prev_close) computed on daily rows strictly
    before ``session_date`` for a symbol. None if not enough prior
    sessions to warm the ATR EMA."""
    if daily_df is None or daily_df.empty:
        return None
    prior = daily_df.loc[daily_df["session_date"] < session_date]
    if len(prior) < atr_span:
        return None
    atr = atr_series(prior["high"], prior["low"], prior["close"], span=atr_span)
    v = atr.iloc[-1]
    if pd.isna(v):
        return None
    return float(v), float(prior["close"].iloc[-1])


def daily_lookback_start(scan_start: date, atr_span: int) -> date:
    """Calendar-day lookback matching the backtester's."""
    needed_bdays = atr_span + 10
    return scan_start - timedelta(days=int(needed_bdays * 7 / 5) + 15)


def enrich_session(
    *,
    symbol: str,
    session_date: date,
    intraday_bars: pd.DataFrame,        # this session's rows only, sorted by ts
    daily_df: pd.DataFrame,             # this symbol's daily bars, one column per usual
    atr_span: int = 14,
) -> Optional[pd.DataFrame]:
    """
    Return the intraday frame with ``vwap`` / ``ema9`` / ``relatr``
    columns added (streamed via ``SymbolSessionState.apply_bar``).
    Returns None if daily prep is not possible (not enough history).
    """
    if intraday_bars is None or intraday_bars.empty:
        return None
    prep = _prep_daily(daily_df, session_date, atr_span)
    if prep is None:
        return None
    atr, prev_close = prep

    session_tz = ZoneInfo(settings.TIMEZONE)
    state = SymbolSessionState(
        symbol         = symbol,
        session_date   = session_date,
        session_tz     = session_tz,
        atr            = atr,
        prev_close     = prev_close,
        rvol_baseline  = {},           # unused here; rvol will read as 0
    )

    vwaps, ema9s, relatrs = [], [], []
    for row in intraday_bars.itertuples(index=False):
        c = CandleRow(
            symbol = symbol,
            open   = float(row.open),
            high   = float(row.high),
            low    = float(row.low),
            close  = float(row.close),
            volume = float(row.volume),
            ts     = row.ts.to_pydatetime(),
        )
        state.apply_bar(c)
        vwaps.append(c.vwap)
        ema9s.append(c.ema9)
        relatrs.append(c.relatr)

    out = intraday_bars.copy()
    out["vwap"]   = vwaps
    out["ema9"]   = ema9s
    out["relatr"] = relatrs
    return out
