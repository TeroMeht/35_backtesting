from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


# <35_backtesting>/backtester/_config.py -> .../<35_backtesting>
PROJECT_ROOT = Path(__file__).resolve().parent.parent


settings = SimpleNamespace(
    BACKFILL_DB_PATH = r"C:\codebase\backtest-data\backfill.duckdb",
    OUTPUT_DIR       = str(PROJECT_ROOT / "output"),
    TIMEZONE         = "Europe/Helsinki",
)
