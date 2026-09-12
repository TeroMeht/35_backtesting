from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


# <35_backtesting>/position_manager/_config.py -> .../<35_backtesting>
PROJECT_ROOT = Path(__file__).resolve().parent.parent


settings = SimpleNamespace(
    OUTPUT_DIR = str(PROJECT_ROOT / "output"),
    TIMEZONE   = "Europe/Helsinki",
)
