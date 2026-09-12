"""
Orchestration for the viewer.

  1. Load the trades CSV.
  2. Read daily + intraday bars for the referenced sessions.
  3. Enrich each unique (symbol, session_date) session's intraday
     bars with vwap / ema9 / relatr via ``SymbolSessionState``.
  4. Build the JSON payload and write the HTML.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from ._config  import settings
from ._data    import open_conn, read_daily, read_intraday
from ._enrich  import daily_lookback_start, enrich_session
from ._input   import load_trades_csv
from ._render  import build_html, build_payload


logger = logging.getLogger(__name__)


def build_viewer(
    *,
    trades_csv: str,
    output_html: str,
    bar_size:   str = "2m",
    atr_span:   int = 14,
    title:      str = "Trade viewer",
) -> str:
    trades = load_trades_csv(trades_csv)
    if trades.empty:
        logger.warning("trades CSV is empty -- rendering an empty viewer.")
        out = build_html(
            title=title,
            payload={"sessions": {}, "trades": []},
            output_path=Path(output_html),
        )
        return str(out)

    symbols  = sorted(trades["symbol"].unique().tolist())
    sess_min = trades["session_date"].min()
    sess_max = trades["session_date"].max()

    conn = open_conn()
    try:
        daily_start = daily_lookback_start(sess_min, atr_span)
        logger.info("reading daily bars for %d symbols in [%s..%s]",
                    len(symbols), daily_start, sess_max)
        daily = read_daily(conn, symbols=symbols,
                           start=daily_start, end=sess_max)
        daily_by_symbol: dict[str, pd.DataFrame] = {
            sym: g.sort_values("session_date").reset_index(drop=True)
            for sym, g in daily.groupby("symbol")
        }

        logger.info("reading %s intraday bars for %d symbols in [%s..%s]",
                    bar_size, len(symbols), sess_min, sess_max)
        intraday = read_intraday(conn, bar_size=bar_size, symbols=symbols,
                                 start=sess_min, end=sess_max)
    finally:
        conn.close()

    bars_by_key: dict[tuple[str, date], pd.DataFrame] = {
        key: g.sort_values("ts").reset_index(drop=True)
        for key, g in intraday.groupby(["symbol", "session_date"])
    }

    needed = {(r.symbol, r.session_date) for r in trades.itertuples(index=False)}
    sessions_bars: dict[tuple[str, date], pd.DataFrame] = {}
    skipped = 0
    for (sym, sd) in sorted(needed):
        bars = bars_by_key.get((sym, sd))
        if bars is None or bars.empty:
            skipped += 1
            continue
        enriched = enrich_session(
            symbol=sym, session_date=sd,
            intraday_bars=bars,
            daily_df=daily_by_symbol.get(sym),
            atr_span=atr_span,
        )
        if enriched is None:
            skipped += 1
            continue
        sessions_bars[(sym, sd)] = enriched

    have_keys = {(k[0], k[1]) for k in sessions_bars.keys()}
    trades = trades[
        trades.apply(
            lambda r: (r["symbol"], r["session_date"]) in have_keys, axis=1,
        )
    ].reset_index(drop=True)

    payload = build_payload(trades, sessions_bars)
    out = build_html(title=title, payload=payload,
                     output_path=Path(output_html))
    logger.info(
        "viewer built: %d trades across %d sessions (skipped %d sessions)",
        len(trades), len(sessions_bars), skipped,
    )
    return str(out)
