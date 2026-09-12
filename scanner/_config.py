from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


# <35_backtesting>/scanner/_config.py -> .../<35_backtesting>
PROJECT_ROOT = Path(__file__).resolve().parent.parent


settings = SimpleNamespace(
    # DuckDB cache filled by 34_dataengine's backfiller. Same default
    # path 34 writes to (out-of-tree so caches survive independent of
    # either project's code); override here if you moved the file.
    # The scanner reads EVERYTHING it needs -- bars AND universe --
    # from this one file; no CSV, no cross-project imports.
    BACKFILL_DB_PATH = r"C:\codebase\backtest-data\backfill.duckdb",

    # Where scan_results.csv files land. Created on first write.
    OUTPUT_DIR       = str(PROJECT_ROOT / "output"),

    # Timezone the DuckDB cache stores intraday ts in, and the zone
    # the 16:30-20:00 intraday window is expressed in. Must match
    # 34_dataengine/_config.TIMEZONE or the day-window math drifts.
    TIMEZONE         = "Europe/Helsinki",
)
