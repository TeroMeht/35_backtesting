"""
Account / risk rules the position manager honors when it sizes and
schedules trades. One dataclass, all knobs; ``__main__.py``
instantiates it once and passes it into the engine.

Everything the engine needs to decide "can I take this trade, and if
so, how large?" lives here -- no strategy questions, only account
mechanics.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rules:
    # ---- Sizing ---------------------------------------------------
    # Fraction of CURRENT equity to risk on a single trade. Risk
    # per share = (entry_price - stop_level); shares_by_risk =
    # floor(equity * risk_per_trade_pct / risk_per_share). Set 0.01
    # for 1% risk.
    risk_per_trade_pct: float

    # Cap on notional (position value at entry) as a fraction of
    # CURRENT equity. Prevents low-stop-distance trades from
    # eating the entire book. Set 1.0 to disable.
    max_position_pct: float

    # Optional whole-shares floor -- skip the trade if sizing lands
    # below this. Set 1 to allow any positive size.
    min_shares: int

    # ---- Concurrency ---------------------------------------------
    # Cap on simultaneously open positions. Extra entries whose ts
    # arrives while the book is full are SKIPPED (never queued).
    max_concurrent_positions: int

    # ---- Costs ---------------------------------------------------
    # Per-share commission charged on BOTH entry and exit.
    commission_per_share: float

    # Fixed per-trade commission charged on BOTH entry and exit.
    commission_per_trade: float

    # ---- Capital -------------------------------------------------
    # Refuse to open a position if entry cost + commission would push
    # cash below zero. Turn off (False) to allow implicit margin at
    # 100% leverage -- cash may go negative and positions still open.
    require_cash_for_entry: bool = True

    # ---- Per-day per-symbol cap ---------------------------------
    # Maximum positions that can be OPENED for the same
    # (symbol, session_date). Subsequent entries for that ticker on
    # the same session are skipped with reason=symbol_day_cap. Only
    # successful opens count -- an entry skipped for another reason
    # (max_concurrent, bad_stop, insufficient_cash, ...) does not
    # burn a slot. Set 0 or a negative value to disable the cap
    # (unlimited entries per symbol per day). 1 = classic "one per
    # ticker per day"; 2 = allow a re-entry after an early stop-out.
    max_positions_per_symbol_per_day: int = 1
