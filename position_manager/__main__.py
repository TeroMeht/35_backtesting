from __future__ import annotations

import logging
from pathlib import Path

from ._config  import settings
from ._output  import write_run_csvs
from ._rules   import Rules
from ._runner  import run


# =============================================================================
# ---- KNOBS ------------------------------------------------------------------
# =============================================================================

RUN_NAME = "3_pm_default"

# Which trades CSV to run the account against. Any CSV with the
# columns listed in ``_input.REQUIRED_COLS`` works here -- the module
# is deliberately decoupled from any single backtest name.
INPUT_TRADES_CSV = str(
    Path(settings.OUTPUT_DIR) / "2_uptrend_reversals_trades.csv"
)

# ---- Capital ----------------------------------------------------------------
STARTING_CAPITAL = 25_000.0

# ---- Risk rules -------------------------------------------------------------
RULES = Rules(
    # Fraction of CURRENT equity risked per trade. 0.01 = 1% risk;
    # 0.005 = 0.5%. Position size is
    #   floor(equity * risk_per_trade_pct / (entry - stop)).
    risk_per_trade_pct       = 0.01,

    # Cap on entry notional as a fraction of CURRENT equity. Guards
    # against tight-stop trades taking oversized positions. 1.0 to
    # disable (leave sizing entirely to risk_per_trade_pct).
    max_position_pct         = 0.6,

    # Minimum whole shares to actually take a trade. Skips
    # under-sized entries rather than opening a token position.
    min_shares               = 1,

    # Max simultaneously open positions. Overlapping entries beyond
    # this get logged to <run>_skips.csv with reason=max_concurrent.
    max_concurrent_positions = 100,

    # Costs. Charged on BOTH entry and exit fills.
    commission_per_share     = 0.0,
    commission_per_trade     = 1.0,

    # When True, refuse entries that would push cash negative; the
    # engine also tries a shrunk size that fits cash. False = allow
    # 100%-leverage implicit margin (cash may go negative).
    require_cash_for_entry   = False,

    # Max positions opened per (symbol, session_date). Once this many
    # positions have been OPENED for a ticker on a given session,
    # further entries for that ticker on that day are skipped with
    # reason=symbol_day_cap. Only successful opens count against the
    # cap -- an entry skipped for another reason (max_concurrent,
    # bad_stop, ...) does not burn a slot. Set 0 (or a negative
    # value) to disable the cap. 1 = classic one-per-day; 2 = allow
    # a re-entry after an early stop-out.
    max_positions_per_symbol_per_day = 3,
)

# =============================================================================


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def main() -> int:
    _configure_logging()

    account = run(
        trades_csv       = INPUT_TRADES_CSV,
        starting_capital = STARTING_CAPITAL,
        rules            = RULES,
    )

    paths = write_run_csvs(account, run_name=RUN_NAME)

    # ---- Summary stats ----
    n_exec  = len(account.executions)
    n_skip  = len(account.skips)
    n_win   = sum(1 for e in account.executions if e.pnl_dollars > 0)
    n_loss  = sum(1 for e in account.executions if e.pnl_dollars < 0)
    total_pnl = sum(e.pnl_dollars for e in account.executions)
    total_commission = sum(e.entry_commission + e.exit_commission
                           for e in account.executions)
    avg_r  = ((sum(e.r_multiple for e in account.executions) / n_exec)
              if n_exec else 0.0)
    equity_peak = max((p.equity for p in account.equity_curve),
                      default=STARTING_CAPITAL)
    equity_final = account.equity()
    ret_pct = ((equity_final - STARTING_CAPITAL) / STARTING_CAPITAL) * 100.0
    win_rate = (n_win / n_exec * 100.0) if n_exec else 0.0

    reason_counts: dict[str, int] = {}
    for s in account.skips:
        reason_counts[s.reason] = reason_counts.get(s.reason, 0) + 1

    print("---- POSITION MANAGER SUMMARY ----")
    print(f"run_name        : {RUN_NAME}")
    print(f"input trades    : {INPUT_TRADES_CSV}")
    print(f"starting capital: {STARTING_CAPITAL:,.2f}")
    print(f"final equity    : {equity_final:,.2f}   "
          f"(peak {equity_peak:,.2f})")
    print(f"total return    : {ret_pct:+.2f}%")
    print(f"executed trades : {n_exec}   "
          f"(wins={n_win} losses={n_loss} win%={win_rate:.1f})")
    print(f"total pnl       : {total_pnl:+,.2f}   "
          f"(commissions {total_commission:,.2f})")
    print(f"avg r-multiple  : {avg_r:+.2f}R")
    if reason_counts:
        parts = "  ".join(f"{k}={v}" for k, v in sorted(reason_counts.items()))
        print(f"skipped entries : {n_skip}   ({parts})")
    else:
        print(f"skipped entries : 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
