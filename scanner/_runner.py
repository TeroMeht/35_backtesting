"""
Orchestration: pull the data batch once, prep per-symbol scalars +
RVOL baselines, then loop (symbol, session_date) through
``scan_symbol_day``.

Everything reads from the local DuckDB cache -- no network in the
scan hot path (see 34_dataengine/backfill-design.md).
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ._config import settings
from ._data   import open_conn, read_daily, read_intraday
from ._prep   import build_rolling_rvol_baselines, prep_daily_scalars
from ._scan   import Filters, Trigger, scan_symbol_day


logger = logging.getLogger(__name__)


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
    db_path: Optional[str] = None,
) -> list[Trigger]:
    """
    Return the list of triggers (one per (symbol, session_date) that
    passed every filter, at the first bar that did so). Writing the
    CSV is the caller's job -- see ``_output.write_triggers_csv``.
    """
    session_tz = ZoneInfo(settings.TIMEZONE)
    conn = open_conn(db_path)
    try:
        # ---- daily prep data ------------------------------------------------
        daily_start = _daily_lookback_start(scan_start, sma_period, atr_span)
        logger.info(
            "reading daily bars for %d symbols in [%s..%s] (sma_period=%d)",
            len(universe), daily_start, scan_end, sma_period,
        )
        t0 = _time.perf_counter()
        daily = read_daily(conn, symbols=universe, start=daily_start, end=scan_end)
        daily_by_symbol: dict[str, pd.DataFrame] = {
            sym: g.sort_values("session_date").reset_index(drop=True)
            for sym, g in daily.groupby("symbol")
        }
        logger.info("  daily read: %d rows, %d symbols with data, %.2fs",
                    len(daily), len(daily_by_symbol), _time.perf_counter() - t0)

        # ---- intraday: baseline lookback + the scan window ------------------
        intraday_start = scan_start - timedelta(days=int(baseline_lookback_days * 7 / 5) + 10)
        logger.info(
            "reading %s intraday bars for %d symbols in [%s..%s]",
            bar_size, len(universe), intraday_start, scan_end,
        )
        t0 = _time.perf_counter()
        intraday = read_intraday(
            conn, bar_size=bar_size, symbols=universe,
            start=intraday_start, end=scan_end,
        )
        logger.info("  intraday read: %d rows, %.2fs",
                    len(intraday), _time.perf_counter() - t0)

        # ---- baselines (ROLLING: one per (symbol, session)) ----------------
        # For each session in the scan window, the baseline uses the
        # `baseline_lookback_days` sessions strictly before it for the
        # same symbol. Sliding window; no year-long freeze.
        t0 = _time.perf_counter()
        baselines = build_rolling_rvol_baselines(
            intraday,
            scan_start   = scan_start,
            scan_end     = scan_end,
            lookback_days = baseline_lookback_days,
            winsor_k     = baseline_winsor_k,
        )
        logger.info("built rolling RVOL baselines for %d (symbol,session) pairs, %.2fs",
                    len(baselines), _time.perf_counter() - t0)

        # Slice the scan-window intraday bars to a per-(symbol,session) view.
        t0 = _time.perf_counter()
        scan_slice = intraday.loc[
            (intraday["session_date"] >= scan_start) &
            (intraday["session_date"] <= scan_end)
        ]
        # Group once; iterate lots.
        bars_by_key: dict[tuple[str, date], pd.DataFrame] = {
            key: g.sort_values("ts").reset_index(drop=True)
            for key, g in scan_slice.groupby(["symbol", "session_date"])
        }
        logger.info("  grouped %d (symbol,session) bar sets, %.2fs",
                    len(bars_by_key), _time.perf_counter() - t0)

    finally:
        conn.close()

    # ---- scan loop ----------------------------------------------------------
    triggers: list[Trigger] = []
    skipped_no_daily     = 0
    skipped_no_baseline  = 0
    skipped_no_bars      = 0
    checked              = 0

    sessions = _business_days(scan_start, scan_end)
    logger.info("scan loop: %d symbols x %d sessions = %d (symbol,session) checks max",
                len(universe), len(sessions), len(universe) * len(sessions))
    scan_t0 = _time.perf_counter()

    # Heartbeat: aim for ~10 progress lines total, at least every 25
    # symbols. Keeps the log light for small universes and readable
    # for large ones.
    heartbeat_every = max(25, len(universe) // 10 or 1)

    for i_sym, symbol in enumerate(universe, start=1):
        logger.debug("scanning %s (%d/%d)", symbol, i_sym, len(universe))
        daily_df = daily_by_symbol.get(symbol)
        for sess in sessions:
            if daily_df is None:
                skipped_no_daily += 1
                continue
            prep = prep_daily_scalars(
                daily_df, session_date=sess,
                atr_span=atr_span, sma_period=sma_period,
            )
            if prep is None:
                skipped_no_daily += 1
                continue
            atr, sma200, prev_close = prep

            bars = bars_by_key.get((symbol, sess))
            if bars is None or bars.empty:
                skipped_no_bars += 1
                continue

            # ROLLING baseline: fresh window per (symbol, session).
            # A missing entry means this symbol didn't have enough
            # prior sessions to seed one (first days of coverage).
            baseline = baselines.get((symbol, sess))
            if baseline is None:
                skipped_no_baseline += 1
                continue

            checked += 1
            trig = scan_symbol_day(
                scan_name    = scan_name,
                symbol       = symbol,
                session_date = sess,
                bars         = bars,
                atr          = atr,
                sma200       = sma200,
                prev_close   = prev_close,
                rvol_baseline = baseline,
                filters      = filters,
                session_tz   = session_tz,
            )
            if trig is not None:
                triggers.append(trig)

        if i_sym % heartbeat_every == 0 or i_sym == len(universe):
            logger.info("  progress: %d/%d symbols, %d triggers so far (%.1fs)",
                        i_sym, len(universe), len(triggers),
                        _time.perf_counter() - scan_t0)

    logger.info(
        "SCAN DONE '%s': triggers=%d checked=%d "
        "skipped_no_daily=%d skipped_no_baseline=%d skipped_no_bars=%d "
        "scan_loop=%.1fs",
        scan_name, len(triggers), checked,
        skipped_no_daily, skipped_no_baseline, skipped_no_bars,
        _time.perf_counter() - scan_t0,
    )
    return triggers
