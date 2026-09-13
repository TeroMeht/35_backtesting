"""
Account state machine: holds cash, open positions, a realized-pnl
tally, and stamps executions and equity-curve rows as the engine
drives it forward through time.

Nothing here is strategy-aware. The engine feeds it (ts, event)
pairs; the account decides what to do given ``Rules``.

Equity model
------------
Because the trades CSV does not include intraday bar prices for the
whole holding window, mark-to-market between entry and exit is not
possible from this input. We therefore mark open positions at
COST (entry price * shares) and only realize on exit. Equity is
recomputed at every event and only changes at events -- the equity
curve is a step function on the entry / exit timestamps.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

from ._input import TradeRow
from ._rules import Rules


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Internal state records
# ------------------------------------------------------------------

@dataclass
class OpenPosition:
    """One live position. Lives in ``Account.open_positions`` from
    the entry event until the exit event that closes it."""
    trade_seq:        int             # index into the input trade list
    symbol:           str
    session_date:     date
    entry_ts:         pd.Timestamp
    entry_price:      float
    stop_level:       float
    shares:           int
    entry_cost:       float           # gross, before commission
    entry_commission: float
    exit_ts:          pd.Timestamp    # planned exit (from the CSV)
    exit_price:       float           # planned exit (from the CSV)
    exit_reason:      str


@dataclass
class Execution:
    """One completed round-trip as the account actually took it.
    Written out to ``<run>_executions.csv``."""
    trade_seq:        int
    backtest_name:    str
    symbol:           str
    session_date:     date

    entry_ts:         pd.Timestamp
    entry_price:      float
    stop_level:       float
    shares:           int
    entry_cost:       float
    entry_commission: float

    exit_ts:          pd.Timestamp
    exit_price:       float
    exit_reason:      str
    exit_proceeds:    float
    exit_commission:  float

    pnl_dollars:      float
    pnl_pct:          float           # on entry_cost (before commissions)
    risk_dollars:     float           # (entry - stop) * shares
    r_multiple:       float           # pnl / risk_dollars
    bars_held:        int

    equity_before:    float
    equity_after:     float


@dataclass
class EquityPoint:
    """One row on the account equity curve, appended at each event
    that changes state (open, close, or skipped-for-record)."""
    ts:              pd.Timestamp
    event:           str              # 'entry' | 'exit' | 'skip'
    symbol:          str
    cash:            float
    open_positions:  int
    open_cost_basis: float
    equity:          float
    note:            str = ""


@dataclass
class Skip:
    """One rejected entry -- kept for a full audit trail."""
    trade_seq:    int
    symbol:       str
    session_date: date
    entry_ts:     pd.Timestamp
    reason:       str
    detail:       str = ""


# ------------------------------------------------------------------
# The state machine
# ------------------------------------------------------------------

@dataclass
class Account:
    starting_capital: float
    rules:            Rules

    cash:               float = field(init=False)
    open_positions:     dict[int, OpenPosition] = field(default_factory=dict)
    executions:         list[Execution]         = field(default_factory=list)
    equity_curve:       list[EquityPoint]       = field(default_factory=list)
    skips:              list[Skip]              = field(default_factory=list)

    # Count of positions successfully OPENED per (symbol, session_date).
    # Incremented in try_open only on a successful open, checked at the
    # top of try_open against rules.max_positions_per_symbol_per_day.
    # Skipped attempts (max_concurrent, bad_stop, ...) do not
    # increment -- they do not burn a slot.
    positions_taken_per_symbol_day: dict[tuple[str, date], int] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self.cash = float(self.starting_capital)

    # -- read-only views ------------------------------------------

    def open_cost_basis(self) -> float:
        return sum(p.entry_cost for p in self.open_positions.values())

    def equity(self) -> float:
        """Cash plus open positions marked at entry cost -- see the
        module docstring for why this can't mark to market."""
        return self.cash + self.open_cost_basis()

    # -- events ---------------------------------------------------

    def try_open(self, seq: int, trade: TradeRow) -> Optional[OpenPosition]:
        """Attempt to open the given trade at its entry_ts. Returns
        the OpenPosition on success; records a Skip and returns None
        on rejection. In either case an EquityPoint is appended."""
        equity_before = self.equity()

        # ---- per-symbol-per-day cap gate ----
        # Runs FIRST so a same-day re-entry beyond the cap doesn't
        # waste cash/sizing checks and doesn't confuse the skip audit.
        # The counter is incremented below only on a successful open,
        # so a skip here reflects opens taken -- not attempts.
        key       = (trade.symbol, trade.session_date)
        cap       = self.rules.max_positions_per_symbol_per_day
        taken     = self.positions_taken_per_symbol_day.get(key, 0)
        if cap > 0 and taken >= cap:
            self._record_skip(
                seq, trade, "symbol_day_cap",
                f"already opened {taken} on {trade.symbol} {trade.session_date} "
                f"(cap={cap})",
            )
            self._stamp_equity(trade.entry_ts, "skip", trade.symbol,
                               note="symbol_day_cap")
            return None


        # ---- concurrency gate ----
        if len(self.open_positions) >= self.rules.max_concurrent_positions:
            self._record_skip(seq, trade, "max_concurrent",
                              f"already holding {len(self.open_positions)}")
            self._stamp_equity(trade.entry_ts, "skip", trade.symbol,
                               note="max_concurrent")
            return None

        # ---- sizing ----
        risk_per_share = trade.entry_price - trade.stop_level
        if risk_per_share <= 0:
            self._record_skip(seq, trade, "bad_stop",
                              f"entry {trade.entry_price} <= stop {trade.stop_level}")
            self._stamp_equity(trade.entry_ts, "skip", trade.symbol,
                               note="bad_stop")
            return None

        risk_dollars       = equity_before * self.rules.risk_per_trade_pct
        shares_by_risk     = int(math.floor(risk_dollars / risk_per_share))
        max_pos_dollars    = equity_before * self.rules.max_position_pct
        shares_by_notional = int(math.floor(max_pos_dollars / trade.entry_price))
        shares = max(0, min(shares_by_risk, shares_by_notional))

        if shares < self.rules.min_shares:
            self._record_skip(seq, trade, "size_below_min",
                              f"sized {shares} < min_shares={self.rules.min_shares}")
            self._stamp_equity(trade.entry_ts, "skip", trade.symbol,
                               note=f"size={shares}")
            return None

        entry_cost       = shares * trade.entry_price
        entry_commission = (self.rules.commission_per_trade
                            + shares * self.rules.commission_per_share)
        total_debit = entry_cost + entry_commission

        if self.rules.require_cash_for_entry and total_debit > self.cash:
            # Try shrinking to what cash allows -- if that still
            # meets min_shares, take it; otherwise skip.
            max_affordable_shares = int(math.floor(
                max(0.0, self.cash - self.rules.commission_per_trade)
                / (trade.entry_price + self.rules.commission_per_share)))
            if max_affordable_shares < self.rules.min_shares:
                self._record_skip(seq, trade, "insufficient_cash",
                                  f"need {total_debit:.2f}, have {self.cash:.2f}")
                self._stamp_equity(trade.entry_ts, "skip", trade.symbol,
                                   note="insufficient_cash")
                return None
            shares           = max_affordable_shares
            entry_cost       = shares * trade.entry_price
            entry_commission = (self.rules.commission_per_trade
                                + shares * self.rules.commission_per_share)
            total_debit = entry_cost + entry_commission

        # ---- open ----
        self.cash -= total_debit
        pos = OpenPosition(
            trade_seq        = seq,
            symbol           = trade.symbol,
            session_date     = trade.session_date,
            entry_ts         = trade.entry_ts,
            entry_price      = trade.entry_price,
            stop_level       = trade.stop_level,
            shares           = shares,
            entry_cost       = entry_cost,
            entry_commission = entry_commission,
            exit_ts          = trade.exit_ts,
            exit_price       = trade.exit_price,
            exit_reason      = trade.exit_reason,
        )
        self.open_positions[seq] = pos
        self._stamp_equity(trade.entry_ts, "entry", trade.symbol)
        self.positions_taken_per_symbol_day[key] = taken + 1
        return pos

    def close(self, seq: int, trade: TradeRow) -> Execution:
        """Close the position opened for ``seq`` at its planned exit
        (fill price and reason from the CSV). Records an Execution
        and an EquityPoint. Raises if the position isn't open."""
        pos = self.open_positions.pop(seq)

        exit_proceeds   = pos.shares * pos.exit_price
        exit_commission = (self.rules.commission_per_trade
                           + pos.shares * self.rules.commission_per_share)
        self.cash += exit_proceeds - exit_commission

        # PnL = (exit - entry) * shares - all commissions
        pnl_dollars = ((pos.exit_price - pos.entry_price) * pos.shares
                       - pos.entry_commission - exit_commission)
        pnl_pct = (pnl_dollars / pos.entry_cost) if pos.entry_cost > 0 else 0.0
        risk_dollars = (pos.entry_price - pos.stop_level) * pos.shares
        r_multiple   = (pnl_dollars / risk_dollars) if risk_dollars > 0 else 0.0

        equity_after = self.equity()
        ex = Execution(
            trade_seq        = seq,
            backtest_name    = trade.backtest_name,
            symbol           = pos.symbol,
            session_date     = pos.session_date,
            entry_ts         = pos.entry_ts,
            entry_price      = pos.entry_price,
            stop_level       = pos.stop_level,
            shares           = pos.shares,
            entry_cost       = pos.entry_cost,
            entry_commission = pos.entry_commission,
            exit_ts          = pos.exit_ts,
            exit_price       = pos.exit_price,
            exit_reason      = pos.exit_reason,
            exit_proceeds    = exit_proceeds,
            exit_commission  = exit_commission,
            pnl_dollars      = pnl_dollars,
            pnl_pct          = pnl_pct,
            risk_dollars     = risk_dollars,
            r_multiple       = r_multiple,
            bars_held        = trade.bars_held,
            equity_before    = equity_after - pnl_dollars,
            equity_after     = equity_after,
        )
        self.executions.append(ex)
        self._stamp_equity(pos.exit_ts, "exit", pos.symbol)
        return ex

    # -- internal helpers -----------------------------------------

    def _record_skip(self, seq: int, trade: TradeRow,
                     reason: str, detail: str) -> None:
        self.skips.append(Skip(
            trade_seq    = seq,
            symbol       = trade.symbol,
            session_date = trade.session_date,
            entry_ts     = trade.entry_ts,
            reason       = reason,
            detail       = detail,
        ))

    def _stamp_equity(self, ts: pd.Timestamp, event: str, symbol: str,
                      note: str = "") -> None:
        self.equity_curve.append(EquityPoint(
            ts              = ts,
            event           = event,
            symbol          = symbol,
            cash            = self.cash,
            open_positions  = len(self.open_positions),
            open_cost_basis = self.open_cost_basis(),
            equity          = self.equity(),
            note            = note,
        ))
