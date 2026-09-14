from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Optional

from ._config import settings
from ._exits  import build_strategy
from ._output import write_trades_csv
from ._runner import run_backtest


# ---- Filters -------------------------------------------------------
# Row-level scan-CSV filter shape. Duck-typed by the engine: only
# ``relatr_min`` (capitulation threshold), ``intraday_start`` and
# ``intraday_end`` are read there; the rest gate which scan rows
# become candidates.
@dataclass
class Filters:
    relatr_min:     float
    cum_volume_min: float
    rvol_min:       float
    require_above_sma200: bool
    intraday_start: time
    intraday_end:   time
    # Premarket % change bounds carried in the scan CSV. Either may be
    # None to disable that side. When a bound is set and a scan row's
    # premarket_change_pct is NaN (session had no premarket data), the
    # row is dropped -- can't confirm a premarket-shaped setup without
    # a premarket number.
    premarket_change_pct_min: Optional[float] = None
    premarket_change_pct_max: Optional[float] = None


# =============================================================================
# ---- KNOBS ------------------------------------------------------------------
# =============================================================================

BACKTEST_NAME = "2_uptrend_reversals"

# Which scan CSV to draw candidates from.
INPUT_SCAN_CSV = str(Path(settings.OUTPUT_DIR) / "1_baseline_scan.csv")

# Intraday resolution to trade on. Match the scan.
BAR_SIZE = "2m"

# Row-level scan filter (same shape as scanner's Filters).
# FILTERS.relatr_min doubles as the capitulation threshold in the entry loop.
FILTERS = Filters(
    relatr_min           = 0.45,
    cum_volume_min       = 500_000,
    rvol_min             = 1.0,
    require_above_sma200 = True,
    intraday_start       = time(16, 30),
    intraday_end         = time(20,  0),
    # Premarket % change gate. Set either to None to disable that side
    premarket_change_pct_min = None,
    premarket_change_pct_max = 2.0,
)

# ---- Entry knobs ------------------------------------------------------------
CAPITULATION_BARS = 8
STOP_OFFSET = 0.02
ATR_SPAN = 14

# ---- Exit knobs -------------------------------------------------------------
#EXIT_STRATEGY = "vwap"
#EXIT_STRATEGY = "eod_exit"  # alternatives: "vwap", "relatr"
EXIT_STRATEGY = "relatr"  # alternatives: "vwap", "eod_exit"

TARGET_DISTANCE = 0.1         # only read by the "vwap" strategy
RELATR_EXIT_THRESHOLD = -0.40   # only read by the "relatr" strategy
SESSION_END = time(23, 0)
EOD_FORCE_CLOSE = True

# =============================================================================


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def main() -> int:
    _configure_logging()

    exit_strategy = build_strategy(
        EXIT_STRATEGY,
        target_distance       = TARGET_DISTANCE,
        relatr_exit_threshold = RELATR_EXIT_THRESHOLD,
    )

    trades = run_backtest(
        backtest_name    = BACKTEST_NAME,
        input_scan_csv   = INPUT_SCAN_CSV,
        filters          = FILTERS,
        bar_size         = BAR_SIZE,
        capitulation_bars = CAPITULATION_BARS,
        stop_offset      = STOP_OFFSET,
        exit_strategy    = exit_strategy,
        session_end      = SESSION_END,
        eod_force_close  = EOD_FORCE_CLOSE,
        atr_span         = ATR_SPAN,
    )

    path = write_trades_csv(trades, backtest_name=BACKTEST_NAME)

    # Summary stats.
    n = len(trades)
    n_target = sum(1 for t in trades if t.exit_reason == "target")
    n_stop   = sum(1 for t in trades if t.exit_reason == "stop")
    n_eod    = sum(1 for t in trades if t.exit_reason == "eod")

    print("---- BACKTEST SUMMARY ----")
    print(f"backtest_name : {BACKTEST_NAME}")
    print(f"input scan    : {INPUT_SCAN_CSV}")
    print(f"bar_size      : {BAR_SIZE}")
    pm_lo = FILTERS.premarket_change_pct_min
    pm_hi = FILTERS.premarket_change_pct_max
    pm_desc = (
        "off" if pm_lo is None and pm_hi is None
        else f"[{'-inf' if pm_lo is None else pm_lo}"
             f"..{'+inf' if pm_hi is None else pm_hi}]%"
    )
    print(f"filters       : relatr>={FILTERS.relatr_min}  "
          f"cum_vol>={int(FILTERS.cum_volume_min):,}  "
          f"rvol>={FILTERS.rvol_min}  "
          f"sma200={'on' if FILTERS.require_above_sma200 else 'off'}  "
          f"premarket_change_pct={pm_desc}")
    print(f"entry window  : {FILTERS.intraday_start}..{FILTERS.intraday_end}    "
          f"session_end : {SESSION_END}")
    print(f"exit strategy : {exit_strategy.name}")
    print(f"stop_offset   : {STOP_OFFSET}    target_distance : {TARGET_DISTANCE}")
    print(f"trades        : {n}   (target={n_target}  stop={n_stop}  eod={n_eod})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
