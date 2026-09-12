"""
Write scan triggers to CSV.

Each run overwrites its own output file (identified by scan_name).
Multiple runs with different filter knobs land in different CSVs so
you can diff / merge / compare them offline.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from ._config import settings
from ._scan   import Trigger


logger = logging.getLogger(__name__)


def write_triggers_csv(
    triggers: Iterable[Trigger],
    *,
    scan_name: str,
    out_dir:   str | Path | None = None,
) -> Path:
    """
    Serialize triggers to ``<OUTPUT_DIR>/<scan_name>.csv`` (created if
    missing). Returns the absolute path written.
    """
    d = Path(out_dir or settings.OUTPUT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{scan_name}.csv"

    rows = [asdict(t) for t in triggers]
    df = pd.DataFrame(rows)
    if df.empty:
        # Write an empty CSV with the header shape so downstream
        # comparison scripts don't crash on a "no triggers" run.
        cols = [
            "scan_name", "symbol", "session_date", "trigger_ts",
            "trigger_time", "open", "high", "low", "close", "volume",
            "vwap", "relatr", "rvol", "cum_volume", "sma200", "atr",
            "prev_close",
        ]
        df = pd.DataFrame(columns=cols)

    df.to_csv(path, index=False)
    logger.info("wrote %d trigger row(s) to %s", len(df), path)
    return path
