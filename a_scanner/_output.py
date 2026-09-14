"""
Write scan triggers to CSV.

Two entry points:

* ``TriggerCSVWriter`` -- streaming context manager used by the
  symbol-chunked scanner. Writes the header immediately so the
  file is well-formed the moment the scan starts, buffers rows,
  and flushes to disk every ``flush_every`` rows so progress is
  visible from a tail. Safe to close mid-scan; the CSV is always
  a proper truncated file, not a half-written line.

* ``write_triggers_csv`` -- classic one-shot writer, kept for
  callers that already have the full trigger list in memory
  (small ad-hoc runs, tests).

Both routes share ``TRIGGER_COLS`` so the header shape stays
identical whichever path a run takes.
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


TRIGGER_COLS: list[str] = [
    "scan_name", "symbol", "session_date", "trigger_ts",
    "trigger_time", "open", "high", "low", "close", "volume",
    "vwap", "relatr", "rvol", "cum_volume", "sma200", "atr",
    "prev_close", "premarket_change_pct",
]


class TriggerCSVWriter:
    """
    Streaming CSV writer used inside the scan loop.

    Usage::

        with TriggerCSVWriter(path) as w:
            for trig in produce_triggers():
                w.write(trig)
            print(w.n_written)

    The header is written on ``__enter__`` so the file has correct
    shape from the start even if no triggers are ever produced
    (matches ``write_triggers_csv``'s empty-CSV behavior). Rows are
    buffered and appended in batches of ``flush_every`` (default 100),
    with a final flush on ``__exit__`` -- so open the file in an
    editor mid-scan and you'll see it filling out in near-real-time.
    """

    def __init__(self, path: str | Path, *, flush_every: int = 100) -> None:
        self.path = Path(path)
        self._flush_every = int(flush_every)
        self._buf: list[dict] = []
        self._n_written = 0

    def __enter__(self) -> "TriggerCSVWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Header-only write; truncates any prior file at this path.
        pd.DataFrame(columns=TRIGGER_COLS).to_csv(
            self.path, index=False, mode="w",
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Always try to flush, even on exception, so partial results
        # aren't lost. If the flush itself fails during exception
        # unwinding, swallow it -- the original exception matters more.
        try:
            self._flush()
        except Exception as flush_exc:
            if exc is None:
                raise
            logger.exception("trailing flush failed: %s", flush_exc)

    def write(self, trigger: Trigger) -> None:
        self._buf.append(asdict(trigger))
        if len(self._buf) >= self._flush_every:
            self._flush()

    def _flush(self) -> None:
        if not self._buf:
            return
        pd.DataFrame(self._buf, columns=TRIGGER_COLS).to_csv(
            self.path, index=False, header=False, mode="a",
        )
        self._n_written += len(self._buf)
        self._buf.clear()

    @property
    def n_written(self) -> int:
        """Rows already committed to disk. Excludes anything still
        sitting in the in-memory buffer."""
        return self._n_written


def write_triggers_csv(
    triggers: Iterable[Trigger],
    *,
    scan_name: str,
    out_dir:   str | Path | None = None,
) -> Path:
    """
    Buffered writer for callers that already have the full trigger
    list in memory. Serializes to ``<OUTPUT_DIR>/<scan_name>.csv``.
    """
    path = triggers_csv_path(scan_name, out_dir=out_dir)
    with TriggerCSVWriter(path, flush_every=10_000) as w:
        for t in triggers:
            w.write(t)
    logger.info("wrote %d trigger row(s) to %s", w.n_written, path)
    return path


def triggers_csv_path(
    scan_name: str,
    *,
    out_dir: str | Path | None = None,
) -> Path:
    """Absolute path where a scan run's triggers CSV lives.
    Doesn't touch the filesystem."""
    d = Path(out_dir or settings.OUTPUT_DIR)
    return d / f"{scan_name}.csv"
