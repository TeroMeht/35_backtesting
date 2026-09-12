"""
Write the position manager's outputs to ``<OUTPUT_DIR>/`` as three
CSVs sharing a common ``<run_name>_`` prefix:

  * <run_name>_executions.csv   one row per completed round-trip
  * <run_name>_equity.csv       one row per state-changing event
  * <run_name>_skips.csv        one row per rejected entry

Column shapes are stable across runs so multiple ``run_name`` outputs
concatenate cleanly for comparison.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from ._account import Account, EquityPoint, Execution, Skip
from ._config  import settings


logger = logging.getLogger(__name__)


_EXEC_COLS = [
    "trade_seq", "backtest_name", "symbol", "session_date",
    "entry_ts", "entry_price", "stop_level", "shares",
    "entry_cost", "entry_commission",
    "exit_ts", "exit_price", "exit_reason",
    "exit_proceeds", "exit_commission",
    "pnl_dollars", "pnl_pct", "risk_dollars", "r_multiple",
    "bars_held", "equity_before", "equity_after",
]

_EQUITY_COLS = [
    "ts", "event", "symbol",
    "cash", "open_positions", "open_cost_basis", "equity", "note",
]

_SKIP_COLS = [
    "trade_seq", "symbol", "session_date", "entry_ts", "reason", "detail",
]


def _write(df: pd.DataFrame, cols: list[str], path: Path) -> None:
    if df.empty:
        df = pd.DataFrame(columns=cols)
    else:
        df = df[cols]
    df.to_csv(path, index=False)
    logger.info("wrote %d row(s) to %s", len(df), path)


def write_run_csvs(
    account: Account,
    *,
    run_name: str,
    out_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Write executions / equity / skips CSVs; return the three
    paths keyed by name for the caller to log or return."""
    d = Path(out_dir or settings.OUTPUT_DIR)
    d.mkdir(parents=True, exist_ok=True)

    exec_path   = d / f"{run_name}_executions.csv"
    equity_path = d / f"{run_name}_equity.csv"
    skip_path   = d / f"{run_name}_skips.csv"

    _write(pd.DataFrame([asdict(e) for e in account.executions]),
           _EXEC_COLS,   exec_path)
    _write(pd.DataFrame([asdict(p) for p in account.equity_curve]),
           _EQUITY_COLS, equity_path)
    _write(pd.DataFrame([asdict(s) for s in account.skips]),
           _SKIP_COLS,   skip_path)

    return {"executions": exec_path, "equity": equity_path, "skips": skip_path}
