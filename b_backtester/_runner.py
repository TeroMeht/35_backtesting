"""
Orchestration for the trades backtester.

  1. Load the scan CSV and row-filter it against ``FILTERS``.
  2. Read daily + intraday bars for the survivor set from DuckDB.
  3. For each (symbol, session_date), prep ATR + prev_close and hand
     the day's intraday bars to ``_engine.run_session``.
  4. Concat every session's Trade rows into one list, return.
"""
from __future__ import annotations

import logging
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ._config   import settings
from ._data     import open_conn, read_daily, read_intraday
from ._engine   import run_session
from ._exits    import ExitStrategy
from ._input    import load_and_filter_scan_csv
from ._prep     import prep_daily_scalars
from ._trade    import Trade


logger = logging.getLogger(__name__)


def _daily_lookback_start(scan_start: date, atr_span: int) -> date:
    needed_bdays = atr_span + 10
    return scan_start - timedelta(days=int(needed_bdays * 7 / 5) + 15)


def run_backtest(
    *,
    backtest_name: str,
    input_scan_csv: str,
    filters,                        # duck-typed
    bar_size: str,
    capitulation_bars: int,
    stop_offset: float,
    exit_strategy: ExitStrategy,
    session_end: time,
    eod_force_close: bool,
    atr_span: int = 14,
) -> list[Trade]:
    session_tz = ZoneInfo(settings.TIMEZONE)

    survivors = load_and_filter_scan_csv(input_scan_csv, filters)
    if survivors.empty:
        logger.warning(
            "no scan rows survived the current filter set -- "
            "widen filters or re-run the scanner first",
        )
        return []

    symbols  = sorted(survivors["symbol"].unique().tolist())
    sess_min = survivors["session_date"].min()
    sess_max = survivors["session_date"].max()

    conn = open_conn()
    try:
        daily_start = _daily_lookback_start(sess_min, atr_span)
        logger.info(
            "reading daily bars for %d symbols in [%s..%s] (atr_span=%d)",
            len(symbols), daily_start, sess_max, atr_span,
        )
        daily = read_daily(conn, symbols=symbols, start=daily_start, end=sess_max)
        daily_by_symbol: dict[str, pd.DataFrame] = {
            sym: g.sort_values("session_date").reset_index(drop=True)
            for sym, g in daily.groupby("symbol")
        }

        logger.info(
            "reading %s intraday bars for %d symbols in [%s..%s]",
            bar_size, len(symbols), sess_min, sess_max,
        )
        intraday = read_intraday(
            conn, bar_size=bar_size, symbols=symbols,
            start=sess_min, end=sess_max,
        )
    finally:
        conn.close()

    bars_by_key: dict[tuple[str, date], pd.DataFrame] = {
        key: g.sort_values("ts").reset_index(drop=True)
        for key, g in intraday.groupby(["symbol", "session_date"])
    }

    trades: list[Trade] = []
    skipped_no_daily = 0
    skipped_no_bars  = 0
    replayed         = 0

    for row in survivors.itertuples(index=False):
        daily_df = daily_by_symbol.get(row.symbol)
        prep = None
        if daily_df is not None:
            prep = prep_daily_scalars(
                daily_df,
                session_date = row.session_date,
                atr_span     = atr_span,
            )
        if prep is None:
            skipped_no_daily += 1
            continue
        atr, prev_close = prep

        bars = bars_by_key.get((row.symbol, row.session_date))
        if bars is None or bars.empty:
            skipped_no_bars += 1
            continue

        # Carry the scan CSV's day-level premarket % change onto every
        # Trade this row produces. NaN in the CSV -> None on the Trade.
        pm_raw = getattr(row, "premarket_change_pct", None)
        scan_pm_pct = (
            None if pm_raw is None or pd.isna(pm_raw) else float(pm_raw)
        )

        replayed += 1
        session_trades = run_session(
            backtest_name    = backtest_name,
            symbol           = row.symbol,
            session_date     = row.session_date,
            bars             = bars,
            atr              = atr,
            prev_close_daily = prev_close,
            filters          = filters,
            capitulation_bars = capitulation_bars,
            stop_offset      = stop_offset,
            exit_strategy    = exit_strategy,
            session_end      = session_end,
            eod_force_close  = eod_force_close,
            session_tz       = session_tz,
            scan_trigger_ts     = row.trigger_ts,
            scan_trigger_relatr = float(row.relatr),
            scan_trigger_rvol   = float(row.rvol),
            scan_premarket_change_pct = scan_pm_pct,
        )
        trades.extend(session_trades)

    # Simple stats for the log line.
    n_target = sum(1 for t in trades if t.exit_reason == "target")
    n_stop   = sum(1 for t in trades if t.exit_reason == "stop")
    n_eod    = sum(1 for t in trades if t.exit_reason == "eod")
    logger.info(
        "BACKTEST DONE '%s': candidates=%d replayed=%d trades=%d "
        "(target=%d stop=%d eod=%d)  skipped_no_daily=%d skipped_no_bars=%d",
        backtest_name, len(survivors), replayed, len(trades),
        n_target, n_stop, n_eod, skipped_no_daily, skipped_no_bars,
    )
    return trades
