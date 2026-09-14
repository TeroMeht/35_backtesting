"""
Per (symbol, session_date) scan loop.

Streams intraday bars from the START of the session through
``SymbolSessionState.apply_bar`` so ``vwap`` / ``rvol`` / ``relatr``
accumulate correctly, but only tests the filter set on bars whose
local-time-of-day falls in the [intraday_start, intraday_end] window.

Selection semantics: at most ONE trigger per (symbol, session). Of
all bars in the intraday window that pass every filter, the bar
with the HIGHEST ``relatr`` is returned -- the day's peak
capitulation moment. Ties on relatr are broken by the earlier
timestamp (deterministic). Bars outside the window still advance
session state (so VWAP / RVOL are correct at any hit inside), but
they never win the selection.

The filter set is expressed as a plain dataclass so a run can swap
thresholds without touching the loop body -- change knobs in
``__main__.py`` and re-run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, time
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from indicators.candle_row    import CandleRow
from indicators.session_state import SymbolSessionState

from ._config import settings


logger = logging.getLogger(__name__)


@dataclass
class Filters:
    """Trigger criteria. All conditions must hold at the same bar."""
    relatr_min:     float
    cum_volume_min: float
    rvol_min:       float
    require_above_sma200: bool
    intraday_start: time
    intraday_end:   time
    # Premarket % change bounds. Either may be None to disable that
    # side. When a bound is set and the day's ``premarket_change_pct``
    # is None (no premarket bars, or prev_close missing / non-positive),
    # the day cannot pass -- a premarket setup can't be confirmed
    # without premarket data.
    premarket_change_pct_min: Optional[float] = None
    premarket_change_pct_max: Optional[float] = None


@dataclass
class Trigger:
    """One row of the output CSV -- the first bar in a session where
    all filters passed. If no bar passed, no Trigger is produced for
    that (symbol, session_date)."""
    scan_name:     str
    symbol:        str
    session_date:  date
    trigger_ts:    pd.Timestamp   # tz-aware Europe/Helsinki
    trigger_time:  time           # local time-of-day, redundant but handy for pivots
    open:          float
    high:          float
    low:           float
    close:         float
    volume:        float
    vwap:          float
    relatr:        float
    rvol:          float
    cum_volume:    float
    sma200:        float
    atr:           float
    prev_close:    float
    # Day-level premarket % change vs prev_close. ``None`` when the
    # session had no premarket bars, or prev_close wasn't positive.
    premarket_change_pct: Optional[float] = None


def scan_symbol_day(
    *,
    scan_name: str,
    symbol: str,
    session_date: date,
    bars: pd.DataFrame,             # this session's intraday bars, sorted by ts
    atr: float,
    sma200: float,
    prev_close: float,
    premarket_change_pct: Optional[float],   # None if no premarket bars this session
    rvol_baseline: dict[time, float],
    filters: Filters,
    session_tz: ZoneInfo,
) -> Optional[Trigger]:
    """
    Return the bar with the HIGHEST ``relatr`` among bars that pass
    every filter in the intraday window, or ``None`` if no bar
    passes. Ties on relatr are broken by the earlier timestamp
    (strict ``>`` on updates). Bars must already be filtered to the
    target session_date and sorted by ts.
    """
    if bars.empty or not rvol_baseline:
        return None

    state = SymbolSessionState(
        symbol         = symbol,
        session_date   = session_date,
        session_tz     = session_tz,
        atr            = atr,
        prev_close     = prev_close,
        rvol_baseline  = rvol_baseline,
    )

    best: Optional[Trigger] = None
    cum_volume = 0.0
    for row in bars.itertuples(index=False):
        candle = CandleRow(
            symbol = symbol,
            open   = float(row.open),
            high   = float(row.high),
            low    = float(row.low),
            close  = float(row.close),
            volume = float(row.volume),
            ts     = row.ts.to_pydatetime(),   # tz-aware Helsinki
        )
        state.apply_bar(candle)
        cum_volume += candle.volume

        local_t: time = candle.ts.astimezone(session_tz).time()
        if local_t < filters.intraday_start or local_t >= filters.intraday_end:
            continue

        # apply_bar sets these; None only if their inputs were missing
        # (atr None, etc.) which we've already prepared against.
        if candle.relatr is None or candle.rvol is None or candle.vwap is None:
            continue

        if candle.relatr < filters.relatr_min:
            continue
        if cum_volume < filters.cum_volume_min:
            continue
        if candle.rvol < filters.rvol_min:
            continue
        if filters.require_above_sma200 and candle.close <= sma200:
            continue
        # Premarket change bounds -- day-level, so evaluated per bar
        # but the value is constant across the session. If a bound is
        # set and we have no premarket value, the day cannot pass.
        if filters.premarket_change_pct_min is not None:
            if (premarket_change_pct is None
                    or premarket_change_pct < filters.premarket_change_pct_min):
                continue
        if filters.premarket_change_pct_max is not None:
            if (premarket_change_pct is None
                    or premarket_change_pct > filters.premarket_change_pct_max):
                continue

        # Passing bar. Keep only if it beats the running best on
        # relatr. Strict ``>`` means a tie leaves the earlier bar
        # (already recorded) in place.
        if best is None or float(candle.relatr) > best.relatr:
            best = Trigger(
                scan_name    = scan_name,
                symbol       = symbol,
                session_date = session_date,
                trigger_ts   = pd.Timestamp(candle.ts),
                trigger_time = local_t,
                open         = candle.open,
                high         = candle.high,
                low          = candle.low,
                close        = candle.close,
                volume       = candle.volume,
                vwap         = float(candle.vwap),
                relatr       = float(candle.relatr),
                rvol         = float(candle.rvol),
                cum_volume   = float(cum_volume),
                sma200       = float(sma200),
                atr          = float(atr),
                prev_close   = float(prev_close),
                premarket_change_pct = premarket_change_pct,
            )

    return best
