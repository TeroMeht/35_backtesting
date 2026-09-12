"""
Pure exit-rule helpers. NO iteration, NO state.

The engine calls these each bar to decide whether an exit fires.
Split from the entry rules on purpose: entry criteria change more
often than exit mechanics, so a change in one file leaves the other
untouched.

Rules currently implemented:

  * ``is_target_hit(relatr, distance)`` -- close within ``distance``
    ATRs of VWAP (either side). Since ``relatr = (vwap - close)/atr``,
    ``|relatr| <= distance`` catches both "just below vwap" and "just
    above vwap". Close-based signal: engine queues the fill for the
    NEXT bar's open.

  * ``is_stop_hit_intrabar(bar_low, stop_level)`` -- the bar's low
    touched or breached the stop. Intrabar signal: engine fills on
    this same bar at ``stop_fill_price``.

  * ``stop_fill_price(bar_open, stop_level)`` -- realistic bar-based
    fill: at the stop level, unless the bar opened past it (gap),
    in which case the fill is the bar's open.
"""
from __future__ import annotations

from typing import Optional


def is_target_hit(candle_relatr: Optional[float], distance: float) -> bool:
    """True when ``|relatr| <= distance`` -- close is within
    ``distance`` ATRs of VWAP on either side."""
    if candle_relatr is None:
        return False
    return abs(float(candle_relatr)) <= float(distance)


def is_stop_hit_intrabar(bar_low: float, stop_level: float) -> bool:
    """True when the bar's low touched or breached the stop level."""
    return float(bar_low) <= float(stop_level)


def stop_fill_price(bar_open: float, stop_level: float) -> float:
    """Realistic bar-based stop fill: min(open, stop). Gap-through
    fills at the open; otherwise at the stop level."""
    return min(float(bar_open), float(stop_level))
