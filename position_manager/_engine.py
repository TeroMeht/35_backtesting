"""
Event loop that drives the Account state machine over a chronological
merge of entry and exit events built from a list of ``TradeRow``s.

Ordering rule: when an entry and an exit share the same timestamp,
the EXIT is processed first. That way freed capital and a freed
concurrency slot from a closing position are available to a new
entry at the same ts -- matching how a real broker would sequence
fills at the bar boundary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from ._account import Account
from ._input   import TradeRow
from ._rules   import Rules


logger = logging.getLogger(__name__)


# Event kinds. Sort priority: exits before entries at the same ts,
# so lower priority number = earlier.
_EV_EXIT  = 0
_EV_ENTRY = 1


@dataclass(frozen=True)
class _Event:
    ts:       pd.Timestamp
    priority: int             # _EV_EXIT | _EV_ENTRY
    seq:      int             # index into trades[]
    kind:     str             # 'entry' | 'exit'


def _build_events(trades: list[TradeRow]) -> list[_Event]:
    """Yield an entry event for every trade at its entry_ts. Exit
    events are only appended dynamically -- a trade whose entry was
    skipped never generates an exit."""
    events: list[_Event] = [
        _Event(ts=t.entry_ts, priority=_EV_ENTRY, seq=i, kind="entry")
        for i, t in enumerate(trades)
    ]
    # Stable sort: (ts asc, priority asc, seq asc). Since we only
    # seed entries here, priority ties don't matter yet; exits are
    # merged on the fly inside run_account.
    events.sort(key=lambda e: (e.ts, e.priority, e.seq))
    return events


def run_account(
    *,
    starting_capital: float,
    rules:            Rules,
    trades:           list[TradeRow],
) -> Account:
    """Feed trades through an Account and return it fully drained.

    We keep a small priority queue of pending exits. At each step we
    peek the earliest of (next entry, next pending exit) and process
    that one. Exits win ties (see ``_EV_EXIT`` < ``_EV_ENTRY``)."""
    import heapq

    account = Account(starting_capital=starting_capital, rules=rules)

    entries = _build_events(trades)      # already time-sorted
    ei = 0                                # cursor into entries[]
    # Heap of pending exits: (ts, seq).
    pending_exits: list[tuple[pd.Timestamp, int]] = []

    def _next_entry_ts() -> pd.Timestamp | None:
        return entries[ei].ts if ei < len(entries) else None

    def _next_exit_ts() -> pd.Timestamp | None:
        return pending_exits[0][0] if pending_exits else None

    while ei < len(entries) or pending_exits:
        n_entry = _next_entry_ts()
        n_exit  = _next_exit_ts()

        # Process an exit first when it exists and is <= the next
        # entry ts. Strict "<=" is what enforces exit-before-entry
        # at ties.
        take_exit = (n_exit is not None
                     and (n_entry is None or n_exit <= n_entry))
        if take_exit:
            _, seq = heapq.heappop(pending_exits)
            account.close(seq, trades[seq])
            continue

        # Otherwise take the next entry.
        ev = entries[ei]
        ei += 1
        opened = account.try_open(ev.seq, trades[ev.seq])
        if opened is not None:
            heapq.heappush(pending_exits, (opened.exit_ts, ev.seq))

    if account.open_positions:
        # Should be impossible -- every opened position has a
        # planned exit in the CSV.
        logger.warning(
            "engine drained but %d positions still open (bug?)",
            len(account.open_positions),
        )

    logger.info(
        "engine done: %d executions, %d skips, cash=%.2f equity=%.2f",
        len(account.executions), len(account.skips),
        account.cash, account.equity(),
    )
    return account
