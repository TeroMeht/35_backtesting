"""
Scanner CLI.

Usage: ``python -m scanner``  -- no flags. Edit the KNOBS block
below to shape a run, then re-run. Each run writes one CSV named
``<SCAN_NAME>.csv`` under the OUTPUT_DIR from ``_config.py`` -- so
running a series of scans with different knobs leaves a stack of
comparable CSVs on disk.

The design mirrors 34_dataengine's ``__main__.py``: one file to
change, no argparse ceremony.
"""
from __future__ import annotations

import logging
from datetime import date, time

from ._config  import settings
from ._data    import load_universe, open_conn
from ._output  import write_triggers_csv
from ._runner  import run_scan
from ._scan    import Filters


# =============================================================================
# ---- KNOBS -- edit these to shape a run -------------------------------------
# =============================================================================

# Name of THIS run. Becomes the CSV filename ("baseline_scan.csv"),
# and lands in every row so a merged compare file stays keyed.
SCAN_NAME = "1_baseline_scan"

SCAN_START = date(2026, 9, 9)
SCAN_END   = date(2026, 9, 9)

BAR_SIZE = "2m"
ATR_SPAN   = 14
SMA_PERIOD = 200
BASELINE_LOOKBACK_DAYS = 5

# ---- Filter thresholds ------------------------------------------------------
# The whole point of many-scans-comparison: change these, re-run,
# diff the resulting CSVs.
FILTERS = Filters(
    relatr_min           = 0.4,
    cum_volume_min       = 100_000,
    rvol_min             = 0.5,
    require_above_sma200 = True,
    intraday_start       = time(16, 30),   # Helsinki
    intraday_end         = time(20,  0),   # Helsinki, exclusive
)

# =============================================================================


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def main() -> int:
    _configure_logging()

    conn = open_conn()
    try:
        universe = load_universe(conn)
    finally:
        conn.close()
    logging.info("loaded %d symbols from DUCKDB", len(universe))

    triggers = run_scan(
        scan_name  = SCAN_NAME,
        universe   = universe,
        scan_start = SCAN_START,
        scan_end   = SCAN_END,
        bar_size   = BAR_SIZE,
        filters    = FILTERS,
        atr_span   = ATR_SPAN,
        sma_period = SMA_PERIOD,
        baseline_lookback_days = BASELINE_LOOKBACK_DAYS,
    )

    path = write_triggers_csv(triggers, scan_name=SCAN_NAME)

    print("---- SCAN SUMMARY ----")
    print(f"scan_name    : {SCAN_NAME}")
    print(f"window       : [{SCAN_START}..{SCAN_END}]  bar_size={BAR_SIZE}")
    print(f"universe     : {len(universe)} symbols")
    print(f"filters      : relatr>={FILTERS.relatr_min}  "
          f"cum_vol>={int(FILTERS.cum_volume_min):,}  "
          f"rvol>={FILTERS.rvol_min}  "
          f"sma200={'on' if FILTERS.require_above_sma200 else 'off'}  "
          f"window={FILTERS.intraday_start}..{FILTERS.intraday_end}")
    print(f"triggers     : {len(triggers)}")
    print(f"csv          : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
