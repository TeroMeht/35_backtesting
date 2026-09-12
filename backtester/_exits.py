"""
Exit strategies -- ONE place to define every "opportunistic" exit
rule the engine knows about.

Pick one by name via ``__main__.EXIT_STRATEGY``. Add new ones here
and register them in ``build_strategy`` at the bottom; the engine
never has to change.

What lives here vs what doesn't
-------------------------------
The engine always runs two exit paths regardless of strategy:

  * **Protective stop** -- intrabar low <= stop_level. Non-optional.
  * **EOD safety-net**  -- if ``EOD_FORCE_CLOSE`` is on, first bar at
                           or after ``SESSION_END`` closes the position
                           at that bar's open with reason ``eod``.

Everything else -- the "target" exit that would give the position up
BEFORE those two fire -- is the strategy's job. Each strategy is
asked once per bar inside the trading-day window:

    "given this candle and the open position, do you want to exit,
     and if so how does it fill?"

A strategy returning ``None`` on every bar collapses the trade to
"stop or EOD" -- exactly what ``HoldUntilEod`` does.

How exits fill
--------------
``fill_at_next_open=True`` (the default) means the CURRENT bar's
close was a signal; the engine queues the exit and fills at the
NEXT bar's open. That mirrors how the VWAP target has always
behaved and preserves gap risk. Set it False for an intrabar close
at ``candle.close`` -- rarely realistic; use with intent.

Adding a new strategy
---------------------
  1. Add a dataclass or function with the ``check(...)`` signature
     below.
  2. Register it in ``build_strategy`` -- give it a short name.
  3. Add knobs to ``__main__.py`` if it needs any, and pass them
     in through ``build_strategy``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from ._position import is_target_hit


# ------------------------------------------------------------------
# Shared decision shape
# ------------------------------------------------------------------

@dataclass(frozen=True)
class ExitDecision:
    """A strategy's answer for one bar. ``reason`` shows up in
    Trade.exit_reason (and in the viewer's colored badge)."""
    reason:            str
    fill_at_next_open: bool = True


class ExitStrategy(Protocol):
    """Per-bar exit oracle. Return None to hold, an ExitDecision to
    exit. Called only inside the trading-day window and only while
    a position is open."""
    name: str
    def check(
        self,
        *,
        candle,               # CandleRow w/ vwap/ema9/relatr populated
        entry_price:  float,
        stop_level:   float,
        bars_held:    int,
    ) -> Optional[ExitDecision]: ...


# ------------------------------------------------------------------
# Concrete strategies
# ------------------------------------------------------------------

@dataclass
class VWAPExit:

    distance: float
    name:     str = "vwap"

    def check(self, *, candle, entry_price, stop_level, bars_held):
        if is_target_hit(candle.relatr, self.distance):
            return ExitDecision(reason="target", fill_at_next_open=True)
        return None


@dataclass
class EODExit:

    name: str = "eod_exit"

    def check(self, *, candle, entry_price, stop_level, bars_held):
        return None


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

# Central name -> strategy factory. Extend this dict when you add a
# strategy above -- an unknown name raises here, so a typo in
# __main__.EXIT_STRATEGY trips at startup instead of silently
# defaulting to something.

def build_strategy(
    name: str,
    *,
    target_distance: float,     # only read by strategies that need it
) -> ExitStrategy:
    if name == "vwap":
        return VWAPExit(distance=target_distance)
    if name == "eod_exit":
        return EODExit()
    raise ValueError(
        f"unknown EXIT_STRATEGY {name!r} -- valid: 'vwap', 'eod_exit' "
        f"(add more in backtester/_exits.py)",
    )
