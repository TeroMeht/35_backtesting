from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parent.parent


settings = SimpleNamespace(
    # Same DuckDB cache the scanner + backtester read.
    BACKFILL_DB_PATH = r"C:\codebase\backtest-data\backfill.duckdb",

    # Where the rendered HTML lands. Created on first write.
    OUTPUT_DIR       = str(PROJECT_ROOT / "output"),

    # Timezone the intraday ts values were tagged with when 34_dataengine
    # wrote them. Bars are rendered in this zone; axis labels show
    # Helsinki wall clock regardless of the viewer's own locale.
    TIMEZONE         = "Europe/Helsinki",
)
