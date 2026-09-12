"""
Orchestration for the position manager.

  1. Load the trades CSV via ``_input.load_trades_csv``.
  2. Feed it through the state machine in ``_engine.run_account``.
  3. Return the fully-drained Account.

Kept small on purpose: the interesting logic is in ``_account`` and
``_engine``, and ``__main__.py`` handles knobs + summary printing.
"""
from __future__ import annotations

import logging

from ._account import Account
from ._engine  import run_account
from ._input   import load_trades_csv
from ._rules   import Rules


logger = logging.getLogger(__name__)


def run(
    *,
    trades_csv:       str,
    starting_capital: float,
    rules:            Rules,
) -> Account:
    trades = load_trades_csv(trades_csv)
    if not trades:
        logger.warning("no trades in %s -- nothing to simulate", trades_csv)
    return run_account(
        starting_capital = starting_capital,
        rules            = rules,
        trades           = trades,
    )
