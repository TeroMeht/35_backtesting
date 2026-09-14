"""
Per-symbol preparation of the scalars ``SymbolSessionState`` needs at
session boot:

  * ``atr``            -- ATR14 as of the previous session's close.
  * ``sma200``         -- SMA of the trailing 200 daily closes ending
                          on the previous session.
  * ``prev_close``     -- previous session's daily close.
  * ``rvol_baseline``  -- dict[time, avg_volume] built from the
                          ``lookback_days`` sessions strictly BEFORE
                          the target session, winsorized per-slot
                          the same way 22/32 do.

Baseline mode: **rolling per session**. For every target session
``s``, the baseline uses the ``lookback_days`` most recent sessions
this symbol has data for that fall before ``s``. On the next
session, the window slides forward by one. No look-ahead: every
prep value uses only data known BEFORE the target session opens.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Optional

import numpy as np
import pandas as pd

from indicators.atr  import atr_series
from indicators.sma  import sma_series


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


def build_rolling_rvol_baselines(
    intraday_df: pd.DataFrame,
    *,
    scan_start: date,
    scan_end:   date,
    lookback_days: int,
    winsor_k: Optional[float] = 3.0,
) -> dict[tuple[str, date], dict[time, float]]:
    """
    Build one ``{time -> avg_volume}`` dict per **(symbol, target
    session)** in ``[scan_start, scan_end]``. For target session
    ``s``, the baseline uses the ``lookback_days`` most recent unique
    sessions this symbol has data for that fall strictly before
    ``s`` (fewer if the symbol doesn't yet have that much history --
    down to a single prior session).

    Per-slot mean is winsorized at ``winsor_k * median`` before
    averaging when ``winsor_k`` is not None, matching the semantics
    of ``indicators.rvol.avg_volume_model``.

    ``intraday_df`` MUST already have ``session_date`` (local) and
    ``ts`` in Europe/Helsinki (i.e. the shape ``_data.read_intraday``
    returns). Bars from BEFORE ``scan_start`` are needed to seed the
    first target sessions' baselines -- pass a frame that reaches
    back at least ``lookback_days`` sessions before ``scan_start``.

    Vectorization: for each symbol we pivot to a
    (session_date x time) matrix once, then slide a window over the
    rows and compute the per-column winsorized mean with numpy
    ops -- so each target session is a handful of vector ops on a
    small (N x ~200) frame, not a fresh groupby.
    """
    if intraday_df is None or intraday_df.empty:
        return {}

    df = intraday_df.copy()
    df["time"] = df["ts"].dt.time

    out: dict[tuple[str, date], dict[time, float]] = {}

    for sym, sym_df in df.groupby("symbol"):
        # Wide: rows = session_date (sorted asc), cols = time slot,
        # values = per-slot volume. Missing slots become NaN.
        pv = sym_df.pivot_table(
            index="session_date",
            columns="time",
            values="volume",
            aggfunc="sum",
        ).sort_index()

        sessions_avail = pv.index.tolist()
        for i, s in enumerate(sessions_avail):
            if not (scan_start <= s <= scan_end):
                continue
            # Prior lookback_days sessions strictly before s.
            window_start = max(0, i - lookback_days)
            if window_start >= i:
                continue                             # no prior sessions
            window = pv.iloc[window_start:i]         # (n_prior x n_slots)

            if winsor_k is not None:
                med = window.median(axis=0)
                # Cap per-column at k * median, but only where median
                # is positive (matches avg_volume_model's guard so a
                # median-0 column isn't zeroed by clip(upper=0)).
                cap = (winsor_k * med).where(med > 0)
                clipped = window.clip(upper=cap, axis=1)
                avg = clipped.mean(axis=0, skipna=True)
            else:
                avg = window.mean(axis=0, skipna=True)

            # Drop slots where the baseline is NaN or zero -- the
            # RVOL formula treats a missing slot as "no baseline
            # contribution", same as before.
            avg = avg.dropna()
            avg = avg[avg > 0]
            if avg.empty:
                continue
            out[(sym, s)] = {t: float(v) for t, v in avg.items()}

    return out
