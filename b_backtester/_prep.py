"""
Per (symbol, session_date) prep: ATR and prev_close from the daily
bars STRICTLY BEFORE the session. That is all
``SymbolSessionState.apply_bar`` needs for correct RelATR + DayAtrExt;
RVOL is not used in the entry/exit rules the backtester currently
implements, so the ``rvol_baseline`` dict can be left empty (a symbol
with an empty baseline will simply produce rvol=0 for every bar,
which the backtester never reads).

If a future exit strategy USES rvol, port scanner/_prep.py:
build_rvol_baselines here as a peer function -- the shape of the
input frame matches, and the winsorization default still lives in
``indicators.rvol.DEFAULT_WINSOR_K``.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

from indicators.atr import atr_series


logger = logging.getLogger(__name__)


def prep_daily_scalars(
    daily_df: pd.DataFrame,
    *,
    session_date: date,
    atr_span: int = 14,
) -> Optional[tuple[float, float]]:
    """
    Given all daily bars for ONE symbol sorted by session_date,
    return ``(atr, prev_close)`` computed on rows strictly before
    ``session_date``. Returns ``None`` when there aren't enough
    prior sessions to warm the ATR EMA.
    """
    if daily_df is None or daily_df.empty:
        return None
    prior = daily_df.loc[daily_df["session_date"] < session_date]
    if len(prior) < atr_span:
        return None
    atr = atr_series(prior["high"], prior["low"], prior["close"], span=atr_span)
    atr_v = atr.iloc[-1]
    if pd.isna(atr_v):
        return None
    return float(atr_v), float(prior["close"].iloc[-1])
