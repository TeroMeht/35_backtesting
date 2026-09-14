"""
Pure entry-trigger helpers for reversal_long. NO iteration, NO state.

The engine (``backtester._engine``) imports these and calls them once
per bar to decide whether the current close is a valid entry signal.
Keeping this file tiny and iteration-free is deliberate -- every rule
change lands in exactly one place and reads on its own.

Entry rules (all must hold on the trigger bar's close):

  1. **Recent capitulation** -- any of the LAST N candles (including
     the current one) has ``relatr >= capitulation_threshold``.
     Ported from 22's ``reversal_shared/detection.detect_capitulation``.

  2. **EMA9 crossover UP** -- ``prev.close < curr.ema9 AND
     curr.close > curr.ema9``. Both compared against the CURRENT bar's
     ema9 (22's ``alarm_logics.is_crossover_up`` convention).

  3. **Trigger closed above VWAP** -- ``relatr < 0``. Since
     ``relatr = (vwap - close) / atr``, a negative relatr means the
     candle closed on the buy-side of VWAP. Rejects "crossed ema9
     but still under vwap" candles.

Fill happens at the NEXT bar's open -- ``_engine`` handles that.

Stop level for a fresh entry is the min of the last N bars' lows
(same window as the capitulation check) minus a fixed offset. See
``compute_stop_level`` below and 22's ``detect_stoplevel``.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence


# ---------------------------------------------------------------------------
# Individual rule predicates.
# ---------------------------------------------------------------------------


def is_ema9_crossover_up(
    prev_close: float, curr_close: float, curr_ema9: float,
) -> bool:
    """22 semantics: prev.close < curr.ema9 AND curr.close > curr.ema9."""
    return prev_close < curr_ema9 and curr_close > curr_ema9


def had_recent_capitulation(
    relatrs: Iterable[float], threshold: float,
) -> bool:
    """Any relatr in the trailing lookback (INCLUDING current bar) is
    at or above the positive capitulation threshold."""
    return any(r >= threshold for r in relatrs)


def is_close_above_vwap(candle_relatr) -> bool:
    """Trigger candle must close ABOVE its own VWAP.
    relatr = (vwap - close) / atr, so relatr < 0 iff close > vwap."""
    return float(candle_relatr) >= 0.3


# ---------------------------------------------------------------------------
# Stop-level helper (used at entry time, then held for the trade's life).
# ---------------------------------------------------------------------------


def compute_stop_level(
    lows: Sequence[float], stop_offset: float,
) -> float:
    """22's detect_stoplevel long side: min(recent lows) - offset,
    rounded to 2dp. Window matches the capitulation lookback."""
    return round(float(min(lows)) - float(stop_offset), 2)


# ---------------------------------------------------------------------------
# Aggregate: does THIS bar fire an entry trigger?
# ---------------------------------------------------------------------------


def is_entry_trigger(
    *,
    prev_close: Optional[float],
    curr_close: float,
    curr_ema9:   Optional[float],
    curr_relatr: Optional[float],
    relatr_window: Sequence[float],
    capitulation_bars: int,
    capitulation_threshold: float,
) -> bool:
    """
    True iff all three rules hold on this bar's close. Returns False
    early on missing history (prev_close / ema9 not yet warm, lookback
    not yet full) so the engine can call this on every bar without
    special-casing warm-up.
    """
    if prev_close is None or curr_ema9 is None:
        return False
    if len(relatr_window) < capitulation_bars:
        return False
    if not had_recent_capitulation(relatr_window, capitulation_threshold):
        return False
    if not is_ema9_crossover_up(prev_close, curr_close, curr_ema9):
        return False
    if not is_close_above_vwap(curr_relatr):
        return False
    return True
