"""
Per (symbol, session_date) trading loop -- entry detection + position
management.

One position per symbol at a time; re-entry is blocked until the
current position exits.

Two Helsinki-local time windows shape the loop:

    * ENTRY WINDOW  = ``[filters.intraday_start, filters.intraday_end)``
      -- new entry signals only fire in here.

    * TRADING DAY   = ``[filters.intraday_start, session_end)``
      -- exit management (stop + target) stays active in the whole
      trading-day window, so a position entered before
      ``intraday_end`` can be held all the way to ``session_end``.

    * At/after ``session_end`` -- if a position is still open and
      ``eod_force_close`` is on, close it on the first bar in this
      out-of-day territory.

Every bar goes through this sequence:

    1) apply_bar        -- indicators up to date for this bar
    2) fill pending_entry via a MARKET-ON-OPEN at this bar's open
       (unconditional; a same-bar gap through the hard stop is the only
        skip -- see the block for details. Single-bar validity, no
        carry-forward -- a new crossover has to form for another chance.)
    3) fill pending strategy exit at candle.open (queued from previous
       bar's close by exit_strategy.check)
    4) roll rolling windows (relatr + low)
    5) EOD force-close on first bar at/after session_end
    6) intrabar stop check     (in trading day, in-position only)
    7) strategy exit check     (in trading day, in-position only)
    8) signal check            (in entry window, flat only; on pass, queue
                                a pending_entry that fills at the NEXT
                                bar's open)

The rules themselves live in tiny pure modules:

    * ``backtester._strategy``  -- entry-trigger predicates + stop
                                    level formula.
    * ``backtester._position``  -- protective-stop and target-hit
                                    predicates + stop fill.
    * ``backtester._exits``     -- named exit strategies. The active
                                    one decides the OPPORTUNISTIC exit
                                    (e.g. VWAP target); protective
                                    stop and EOD safety-net always run.

so the engine only sequences state transitions -- it never encodes
the rules. Change a rule, change one of these peer modules and this
loop keeps working.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from datetime import date, time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from indicators.candle_row    import CandleRow
from indicators.session_state import SymbolSessionState

from ._exits    import ExitStrategy
from ._position import is_stop_hit_intrabar, stop_fill_price
from ._strategy import (
    compute_stop_level, is_entry_trigger,
    had_recent_capitulation, is_ema9_crossover_up, is_close_above_vwap,
)
from ._trade    import Trade


logger = logging.getLogger(__name__)


def _candle_from_row(row, symbol: str) -> CandleRow:
    return CandleRow(
        symbol = symbol,
        open   = float(row.open),
        high   = float(row.high),
        low    = float(row.low),
        close  = float(row.close),
        volume = float(row.volume),
        ts     = row.ts.to_pydatetime(),
    )


def _trigger_snapshot(candle: CandleRow, ts, relatr_window, low_window,
                      stop_level) -> dict:
    """Capture everything the Trade row needs to remember about the
    trigger bar; carried on ``pending_entry`` until fill."""
    return {
        "trigger_ts":     ts,
        "trigger_open":   float(candle.open),
        "trigger_high":   float(candle.high),
        "trigger_low":    float(candle.low),
        "trigger_close":  float(candle.close),
        "trigger_ema9":   float(candle.ema9),
        "trigger_vwap":   float(candle.vwap) if candle.vwap is not None else float("nan"),
        "trigger_relatr": float(candle.relatr) if candle.relatr is not None else float("nan"),
        "trigger_rvol":   float(candle.rvol) if candle.rvol is not None else None,
        "max_recent_relatr": float(max(relatr_window)),
        "lookback_low":      float(min(low_window)),
        "stop_level":        stop_level,
    }


def run_session(
    *,
    backtest_name: str,
    symbol: str,
    session_date: date,
    bars: pd.DataFrame,
    atr: float,
    prev_close_daily: float,
    filters,                       # duck-typed
    capitulation_bars: int,
    stop_offset: float,
    exit_strategy: ExitStrategy,   # opportunistic-exit oracle (see _exits.py)
    session_end: time,             # trading-day cutoff (Helsinki-local)
    eod_force_close: bool,
    session_tz: ZoneInfo,
    # Scanner-CSV context to carry onto every emitted Trade.
    scan_trigger_ts:     pd.Timestamp,
    scan_trigger_relatr: float,
    scan_trigger_rvol:   float,
    # Day-level premarket % change from the scan CSV. None when the
    # scan had no premarket data for that session.
    scan_premarket_change_pct: Optional[float] = None,
) -> list[Trade]:
    """
    Replay one (symbol, session_date). Emit one Trade per completed
    round-trip. Empty list means no trades were taken.
    """
    if bars is None or bars.empty:
        return []

    state = SymbolSessionState(
        symbol         = symbol,
        session_date   = session_date,
        session_tz     = session_tz,
        atr            = atr,
        prev_close     = prev_close_daily,
        rvol_baseline  = {},           # unused here
    )

    relatr_window: deque[float] = deque(maxlen=capitulation_bars)
    low_window:    deque[float] = deque(maxlen=capitulation_bars)

    trades: list[Trade] = []

    # ------- diagnostic counters (only read by the end-of-session
    # "no trade" log; do not affect any decision) -----------------
    n_bars_in_window     = 0   # bars where entry check actually ran
    n_cap_hits           = 0   # trailing relatr window had a >= relatr_min
    n_crossover_hits     = 0   # prev.close < ema9 AND close > ema9 this bar
    n_relatr_gate_hits   = 0   # this bar had relatr >= 0.3 (misnamed vwap gate)
    n_full_trigger       = 0   # is_entry_trigger returned True
    n_signals_below_stop = 0   # next-bar open gapped through the stop

    # Position state.
    in_position:       bool                    = False
    entry_ts:          Optional[pd.Timestamp]  = None
    entry_price:       Optional[float]         = None
    entry_bar_idx:     Optional[int]           = None
    stop_level:        Optional[float]         = None
    trigger_snapshot:  Optional[dict]          = None

    # Pending fills (signal at bar N close -> fill at bar N+1 open).
    pending_entry:       Optional[dict] = None
    # Strategy-selected opportunistic exit queued for next bar's open;
    # None means "hold". Carries the exit_reason string the strategy
    # returned (typically "target").
    pending_exit_reason: Optional[str]  = None

    prev_close: Optional[float] = None
    eod_done:   bool = False

    def _close_position(exit_ts_, exit_price_, reason, cur_bar_idx):
        """Emit a Trade row and reset position state to flat."""
        nonlocal in_position, entry_ts, entry_price, entry_bar_idx
        nonlocal stop_level, trigger_snapshot, pending_exit_reason
        pnl     = float(exit_price_) - float(entry_price)
        pnl_pct = pnl / float(entry_price) if entry_price else 0.0
        bars_held = int(cur_bar_idx - entry_bar_idx) if entry_bar_idx is not None else 0
        trades.append(Trade(
            backtest_name = backtest_name,
            symbol        = symbol,
            session_date  = session_date,
            trigger_ts        = trigger_snapshot["trigger_ts"],
            trigger_open      = trigger_snapshot["trigger_open"],
            trigger_high      = trigger_snapshot["trigger_high"],
            trigger_low       = trigger_snapshot["trigger_low"],
            trigger_close     = trigger_snapshot["trigger_close"],
            trigger_ema9      = trigger_snapshot["trigger_ema9"],
            trigger_vwap      = trigger_snapshot["trigger_vwap"],
            trigger_relatr    = trigger_snapshot["trigger_relatr"],
            trigger_rvol      = trigger_snapshot["trigger_rvol"],
            max_recent_relatr = trigger_snapshot["max_recent_relatr"],
            lookback_low      = trigger_snapshot["lookback_low"],
            entry_ts    = entry_ts,
            entry_price = float(entry_price),
            stop_level  = float(stop_level),
            exit_ts     = exit_ts_,
            exit_price  = float(exit_price_),
            exit_reason = reason,
            pnl         = pnl,
            pnl_pct     = pnl_pct,
            bars_held   = bars_held,
            scan_trigger_ts     = scan_trigger_ts,
            scan_trigger_relatr = float(scan_trigger_relatr),
            scan_trigger_rvol   = float(scan_trigger_rvol),
            scan_premarket_change_pct = scan_premarket_change_pct,
        ))
        in_position       = False
        entry_ts          = None
        entry_price       = None
        entry_bar_idx     = None
        stop_level        = None
        trigger_snapshot  = None
        pending_exit_reason = None

    for i, row in enumerate(bars.itertuples(index=False)):
        candle = _candle_from_row(row, symbol)
        ts_    = pd.Timestamp(candle.ts)
        local_t: time = candle.ts.astimezone(session_tz).time()
        in_entry_window = filters.intraday_start <= local_t < filters.intraday_end
        in_trading_day  = filters.intraday_start <= local_t < session_end

        # 1) advance indicator state ---------------------------------------
        state.apply_bar(candle)

        # 2) fill pending_entry --------------------------------------------
        # Signal-candle model: the previous bar was the ema9 crossover
        # (the SIGNAL candle). Entry is a MARKET-ON-OPEN at THIS bar --
        # we fill unconditionally at this bar's open. No stop-buy level,
        # no "next-bar high must clear signal high" gate: a legitimate
        # ema9 crossover shouldn't be dropped just because the next bar
        # happens to open flat or gap slightly down.
        #
        # Sanity gate kept: if the fill would already be at or below the
        # hard stop level (i.e. the next-bar open gapped down through
        # the trailing-lows stop), skip the entry -- a same-bar zero-or-
        # worse trade adds noise, not signal.
        if pending_entry is not None:
            if not in_position:
                sig      = pending_entry
                stop_lvl = sig["stop_level"]
                fill     = float(candle.open)
                if fill > stop_lvl:
                    in_position      = True
                    entry_ts         = ts_
                    entry_price      = fill
                    entry_bar_idx    = i
                    stop_level       = stop_lvl
                    trigger_snapshot = sig
                else:
                    n_signals_below_stop += 1
                # Consume the signal regardless of outcome (single-bar validity).
                pending_entry = None
            else:
                # Defensive: cannot normally be in position with pending_entry
                pending_entry = None

        # 3) fill pending strategy exit at this bar's open -----------------
        # The strategy returned a next-open exit last bar; consume it
        # here, tagged with whatever reason string it supplied.
        if pending_exit_reason is not None and in_position:
            _close_position(
                exit_ts_=ts_, exit_price_=float(candle.open),
                reason=pending_exit_reason, cur_bar_idx=i,
            )

        # 4) roll rolling windows (post apply_bar so relatr is current) ----
        r = candle.relatr if candle.relatr is not None else -math.inf
        relatr_window.append(r)
        low_window.append(float(candle.low))

        # 5) EOD force-close on first bar at/after session_end -------------
        # A position entered anywhere in the entry window can be held all
        # the way to session_end; only when we cross that line does EOD
        # kick in. Fills at that bar's open.
        if not in_trading_day and in_position and eod_force_close and not eod_done:
            _close_position(
                exit_ts_=ts_, exit_price_=float(candle.open),
                reason="eod", cur_bar_idx=i,
            )
            eod_done = True

        if not in_trading_day:
            # Pre-open or post-session_end: no signals, no management.
            # Still update prev_close so the FIRST in-entry-window bar
            # inherits the last known close for its crossover check.
            prev_close = float(candle.close)
            continue

        # 6) intrabar stop check -------------------------------------------
        # Active anywhere in the trading day, including after intraday_end
        # (the entry cutoff) so a position taken late can still be stopped.
        if in_position and is_stop_hit_intrabar(candle.low, stop_level):
            fill = stop_fill_price(candle.open, stop_level)
            _close_position(
                exit_ts_=ts_, exit_price_=fill,
                reason="stop", cur_bar_idx=i,
            )
            prev_close = float(candle.close)
            continue

        # 7) strategy-driven opportunistic exit ----------------------------
        # Same trading-day scope as the stop. The active exit strategy
        # (see backtester/_exits.py) is asked whether to close on this
        # bar. Returning None means "hold"; returning an ExitDecision
        # either queues a next-open fill (default) or closes on this
        # bar's close intrabar.
        if in_position:
            decision = exit_strategy.check(
                candle       = candle,
                entry_price  = float(entry_price),
                stop_level   = float(stop_level),
                bars_held    = i - entry_bar_idx,
            )
            if decision is not None:
                if decision.fill_at_next_open:
                    pending_exit_reason = decision.reason
                else:
                    _close_position(
                        exit_ts_=ts_, exit_price_=float(candle.close),
                        reason=decision.reason, cur_bar_idx=i,
                    )
                    prev_close = float(candle.close)
                    continue

        # 8) entry trigger check (entry window only, flat only) -------------
        if in_entry_window and not in_position and pending_entry is None:
            n_bars_in_window += 1
            # Sub-condition hits for the end-of-session diagnostic.
            # Mirrors is_entry_trigger's warmup check so a bar only
            # contributes to the breakdown once it can be a trigger
            # at all.
            if (prev_close is not None
                    and candle.ema9 is not None
                    and len(relatr_window) >= capitulation_bars):
                if had_recent_capitulation(relatr_window, filters.relatr_min):
                    n_cap_hits += 1
                if is_ema9_crossover_up(
                    prev_close, float(candle.close), candle.ema9,
                ):
                    n_crossover_hits += 1
                if candle.relatr is not None and is_close_above_vwap(candle.relatr):
                    n_relatr_gate_hits += 1

            if is_entry_trigger(
                prev_close   = prev_close,
                curr_close   = float(candle.close),
                curr_ema9    = candle.ema9,
                curr_relatr  = candle.relatr,
                relatr_window = relatr_window,
                capitulation_bars      = capitulation_bars,
                capitulation_threshold = filters.relatr_min,
            ):
                n_full_trigger += 1
                stop_at_signal = compute_stop_level(low_window, stop_offset)
                pending_entry  = _trigger_snapshot(
                    candle, ts_, relatr_window, low_window, stop_at_signal,
                )

        prev_close = float(candle.close)

    if not trades:
        # Pick the tightest miss the counters can prove.
        if n_bars_in_window == 0:
            reason = "no bars in entry window"
        elif n_full_trigger > 0:
            # Under market-on-open fills the only reason a signal can
            # produce no trade is the same-bar stop gap. If a signal
            # neither filled nor tripped the stop-gap, it's a rare
            # defensive branch (see the pending_entry block).
            parts = [f"{n_full_trigger} signal(s) fired"]
            if n_signals_below_stop:
                parts.append(
                    f"{n_signals_below_stop} would-fill at/under stop"
                )
            else:
                parts.append("no fill recorded")
            reason = "; ".join(parts)
        else:
            reason = (
                f"no full trigger in {n_bars_in_window} in-window bars "
                f"(cap={n_cap_hits} crossover={n_crossover_hits} "
                f"relatr>=0.3={n_relatr_gate_hits})"
            )
        logger.info(
            "%s %s: replayed %d bars, no trade -- %s",
            symbol, session_date, len(bars), reason,
        )
    return trades
