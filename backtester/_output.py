"""
Write Trade rows to ``<OUTPUT_DIR>/<BACKTEST_NAME>_trades.csv``.

One row per completed round-trip trade. Column shape is stable so
multiple backtest runs concatenate cleanly for comparison.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from ._config import settings
from ._trade  import Trade


logger = logging.getLogger(__name__)


_COLS = [
    "backtest_name", "symbol", "session_date",
    "trigger_ts",
    "trigger_open", "trigger_high", "trigger_low", "trigger_close",
    "trigger_ema9", "trigger_vwap", "trigger_relatr", "trigger_rvol",
    "max_recent_relatr", "lookback_low",
    "entry_ts", "entry_price", "stop_level",
    "exit_ts", "exit_price", "exit_reason",
    "pnl", "pnl_pct", "bars_held",
    "scan_trigger_ts", "scan_trigger_relatr", "scan_trigger_rvol",
]


def write_trades_csv(
    trades: Iterable[Trade],
    *,
    backtest_name: str,
    out_dir: str | Path | None = None,
) -> Path:
    d = Path(out_dir or settings.OUTPUT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{backtest_name}_trades.csv"

    rows = [asdict(t) for t in trades]
    df = pd.DataFrame(rows, columns=_COLS if not rows else None)
    if rows:
        df = df[_COLS]
    df.to_csv(path, index=False)
    logger.info("wrote %d trade row(s) to %s", len(df), path)
    return path
