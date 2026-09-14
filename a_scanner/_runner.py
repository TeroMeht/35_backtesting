"""
Orchestration: loop the universe ONE SYMBOL AT A TIME and stream
triggers straight to CSV.

Design notes -- why symbol-chunked, not full-batch:

Loading the whole universe's intraday for a year at once is a
multi-GB pandas frame plus a ~500K-entry ``bars_by_key`` dict --
the process OOMs long before the scan loop actually starts. Doing
one symbol at a time bounds peak memory to whatever ONE symbol's
year of 2min bars needs (~50K rows, a few MB), regardless of
universe size.

Per symbol we:
  1. Read that symbol's daily bars and precompute its
     ``(atr, sma200, prev_close)`` timeline once (was: recomputed
     inside the session loop for every session).
  2. Read that symbol's intraday for the scan window plus
     ``baseline_lookback_days`` of prior sessions.
  3. Build rolling RVOL baselines for this symbol only.
  4. Scan its sessions, streaming any triggers to
     ``TriggerCSVWriter`` as they land -- the CSV fills out live,
     and if the process is killed mid-scan you keep everything up
     to the last flush.
  5. Drop all of the above before moving on. Peak memory is
     effectively one symbol at a time.

Everything reads from the local DuckDB cache -- no network in the
scan hot path (see 34_dataengine/backfill-design.md).
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from indicators.atr              import atr_series
from indicators.sma              import sma_series
from indicators.premarket_change import (
    DEFAULT_MARKET_OPEN, next_premarket_change_pct,
)

from ._config import settings
from ._data   import open_conn, read_daily, read_intraday
from ._output import TriggerCSVWriter, triggers_csv_path
from ._prep   import build_rolling_rvol_baselines
from ._scan   import Filters, scan_symbol_day


logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    """Return value of ``run_scan``. All counts are aggregates over
    the whole run; the actual trigger rows are in ``csv_path``."""
    scan_name:           str
    csv_path:            Path
    n_symbols_universe:  int
    n_symbols_scanned:   int          # had both daily and intraday data
    n_symbols_no_data:   int          # skipped entirely (no daily OR no intraday)
    n_checked:           int          # (symbol, session) pairs that ran scan_symbol_day
    n_triggers:          int
    skipped_no_daily:    int          # not enough daily history for SMA/ATR
    skipped_no_bars:     int          # no intraday bars for that session
    skipped_no_baseline: int          # not enough prior sessions to seed RVOL baseline
    elapsed_seconds:     float


def _business_days(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start=start, end=end)]


def _daily_lookback_start(scan_start: date, sma_period: int, atr_span: int) -> date:
    """
    Calendar-day lookback that guarantees at least ``sma_period``
    business days of daily bars before ``scan_start`` (plus a cushion
    for ATR warmup, weekends, and market holidays).
    """
    needed_bdays = sma_period + atr_span + 10
    # 7/5 calendar-to-business ratio + a two-week holiday cushion.
    return scan_start - timedelta(days=int(needed_bdays * 7 / 5) + 15)


def _build_daily_prep_lookup(
    daily_df: pd.DataFrame,
    *,
    atr_span:   int,
    sma_period: int,
) -> Optional[dict[date, tuple[float, float, float]]]:
    """
    Precompute the ``(atr, sma200, prev_close)`` triple that
    ``prep_daily_scalars`` would produce for every possible target
    session, in a single pass over this symbol's daily bars.

    Returns ``dict[target_session -> (atr, sma, prev_close)]`` where
    the values are what ``prep_daily_scalars(session_date=target)``
    would return. Target sessions between daily row ``i`` and daily
    row ``i+1`` all get row ``i``'s values (a market holiday reads
    yesterday's ATR, same as the original code).

    Returns ``None`` if the symbol has fewer than ``sma_period``
    daily bars -- the caller then skips the symbol entirely.
    """
    if daily_df is None or daily_df.empty:
        return None
    df = daily_df.sort_values("session_date").reset_index(drop=True)
    if len(df) < sma_period:
        return None

    atrs = atr_series(df["high"], df["low"], df["close"], span=atr_span)
    smas = sma_series(df["close"], sma_period)

    lookup: dict[date, tuple[float, float, float]] = {}
    sessions = df["session_date"].tolist()
    closes   = df["close"].tolist()
    # For target session T, the correct row is the LAST row with
    # session_date < T. So walk daily rows and stamp every calendar
    # day up to (but not including) the NEXT daily row with this
    # row's values.
    next_sessions = sessions[1:] + [None]
    for i, (row_date, next_date) in enumerate(zip(sessions, next_sessions)):
        atr_v = atrs.iloc[i]
        sma_v = smas.iloc[i]
        if pd.isna(atr_v) or pd.isna(sma_v):
            continue
        prep = (float(atr_v), float(sma_v), float(closes[i]))

        # Fill targets in (row_date, next_date]. When next_date is
        # None we extend a bit past the last daily row so targets
        # after that use its values (matches prep_daily_scalars).
        target = row_date + timedelta(days=1)
        stop = next_date if next_date is not None else (row_date + timedelta(days=400))
        while target <= stop:
            lookup[target] = prep
            target += timedelta(days=1)

    return lookup


def run_scan(
    *,
    scan_name: str,
    universe:  list[str],
    scan_start: date,
    scan_end:   date,
    bar_size:   str,
    filters:    Filters,
    atr_span:   int = 14,
    sma_period: int = 200,
    baseline_lookback_days: int = 20,
    baseline_winsor_k: Optional[float] = 3.0,
    csv_path:   Optional[Path] = None,
    db_path:    Optional[str]  = None,
    flush_every: int = 50,
) -> ScanSummary:
    """
    Symbol-chunked scan. Loops the universe one symbol at a time so
    peak memory stays bounded no matter how long the scan window is,
    and streams triggers to ``csv_path`` as they land.

    Returns a ``ScanSummary`` (counts + csv path). The full trigger
    list is NOT kept in memory -- read it from ``csv_path`` if you
    need it.
    """
    if csv_path is None:
        csv_path = triggers_csv_path(scan_name)
    csv_path = Path(csv_path)

    session_tz     = ZoneInfo(settings.TIMEZONE)
    sessions       = _business_days(scan_start, scan_end)
    daily_start    = _daily_lookback_start(scan_start, sma_period, atr_span)
    intraday_start = scan_start - timedelta(
        days=int(baseline_lookback_days * 7 / 5) + 10
    )

    logger.info(
        "SCAN START '%s': %d symbols x %d sessions "
        "[%s..%s]  bar_size=%s  daily_start=%s  intraday_start=%s",
        scan_name, len(universe), len(sessions),
        scan_start, scan_end, bar_size, daily_start, intraday_start,
    )
    logger.info("  streaming triggers to %s", csv_path)

    # Aim for ~20 progress lines over the whole run so the operator
    # sees steady heartbeat on big universes without log spam on
    # small ones.
    heartbeat_every = max(1, len(universe) // 20)

    n_symbols_scanned   = 0
    n_symbols_no_data   = 0
    n_checked           = 0
    n_triggers          = 0
    skipped_no_daily    = 0
    skipped_no_bars     = 0
    skipped_no_baseline = 0

    scan_t0 = _time.perf_counter()

    conn = open_conn(db_path)
    try:
        with TriggerCSVWriter(csv_path, flush_every=flush_every) as writer:
            for i_sym, symbol in enumerate(universe, start=1):
                # ---- daily for this symbol ----
                daily = read_daily(conn, symbols=[symbol],
                                   start=daily_start, end=scan_end)
                daily_prep = _build_daily_prep_lookup(
                    daily, atr_span=atr_span, sma_period=sma_period,
                )
                if daily_prep is None:
                    n_symbols_no_data += 1
                    _maybe_heartbeat(i_sym, len(universe), n_triggers,
                                     heartbeat_every, scan_t0)
                    continue

                # ---- intraday for this symbol ----
                intraday = read_intraday(
                    conn, bar_size=bar_size, symbols=[symbol],
                    start=intraday_start, end=scan_end,
                )
                if intraday.empty:
                    n_symbols_no_data += 1
                    _maybe_heartbeat(i_sym, len(universe), n_triggers,
                                     heartbeat_every, scan_t0)
                    continue

                # ---- rolling RVOL baselines for this symbol ----
                baselines = build_rolling_rvol_baselines(
                    intraday,
                    scan_start   = scan_start,
                    scan_end     = scan_end,
                    lookback_days = baseline_lookback_days,
                    winsor_k     = baseline_winsor_k,
                )

                # ---- group scan-window bars by session ----
                scan_slice = intraday.loc[
                    (intraday["session_date"] >= scan_start)
                    & (intraday["session_date"] <= scan_end)
                ]
                bars_by_session: dict[date, pd.DataFrame] = {
                    sd: g.sort_values("ts").reset_index(drop=True)
                    for sd, g in scan_slice.groupby("session_date")
                }

                n_symbols_scanned += 1

                # ---- scan loop for this symbol ----
                for sess in sessions:
                    prep = daily_prep.get(sess)
                    if prep is None:
                        skipped_no_daily += 1
                        continue
                    atr, sma200, prev_close = prep

                    bars = bars_by_session.get(sess)
                    if bars is None or bars.empty:
                        skipped_no_bars += 1
                        continue

                    baseline = baselines.get((symbol, sess))
                    if baseline is None:
                        skipped_no_baseline += 1
                        continue

                    # Day-level premarket % change: last close strictly
                    # before market open vs prev daily close. None when
                    # the session has no premarket bars (weekend fill,
                    # early trading halt, etc.) -- next_premarket_change_pct
                    # also returns None on missing / non-positive prev_close.
                    local_times = bars["ts"].dt.tz_convert(session_tz).dt.time
                    pm_mask     = local_times < DEFAULT_MARKET_OPEN
                    if pm_mask.any():
                        pm_last = float(bars.loc[pm_mask, "close"].iloc[-1])
                        premarket_change_pct = next_premarket_change_pct(
                            pm_last, prev_close,
                        )
                    else:
                        premarket_change_pct = None

                    n_checked += 1
                    trig = scan_symbol_day(
                        scan_name    = scan_name,
                        symbol       = symbol,
                        session_date = sess,
                        bars         = bars,
                        atr          = atr,
                        sma200       = sma200,
                        prev_close   = prev_close,
                        premarket_change_pct = premarket_change_pct,
                        rvol_baseline = baseline,
                        filters      = filters,
                        session_tz   = session_tz,
                    )
                    if trig is not None:
                        writer.write(trig)
                        n_triggers += 1

                # Drop this symbol's data before moving on -- makes
                # the intent explicit even though Python would free
                # them at loop-end anyway.
                del daily, daily_prep, intraday, baselines, bars_by_session

                _maybe_heartbeat(i_sym, len(universe), n_triggers,
                                 heartbeat_every, scan_t0)
    finally:
        conn.close()

    elapsed = _time.perf_counter() - scan_t0
    logger.info(
        "SCAN DONE '%s': triggers=%d checked=%d "
        "symbols_scanned=%d symbols_no_data=%d "
        "skipped_no_daily=%d skipped_no_baseline=%d skipped_no_bars=%d "
        "elapsed=%.1fs",
        scan_name, n_triggers, n_checked,
        n_symbols_scanned, n_symbols_no_data,
        skipped_no_daily, skipped_no_baseline, skipped_no_bars,
        elapsed,
    )

    return ScanSummary(
        scan_name            = scan_name,
        csv_path             = csv_path,
        n_symbols_universe   = len(universe),
        n_symbols_scanned    = n_symbols_scanned,
        n_symbols_no_data    = n_symbols_no_data,
        n_checked            = n_checked,
        n_triggers           = n_triggers,
        skipped_no_daily     = skipped_no_daily,
        skipped_no_bars      = skipped_no_bars,
        skipped_no_baseline  = skipped_no_baseline,
        elapsed_seconds      = elapsed,
    )


def _maybe_heartbeat(
    i_sym: int,
    n_total: int,
    n_triggers: int,
    every: int,
    t0: float,
) -> None:
    if i_sym % every == 0 or i_sym == n_total:
        logger.info(
            "  progress: %d/%d symbols (%.0f%%), %d triggers so far (%.1fs)",
            i_sym, n_total, 100.0 * i_sym / n_total,
            n_triggers, _time.perf_counter() - t0,
        )
