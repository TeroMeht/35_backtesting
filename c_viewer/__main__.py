from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

from ._config import settings
from ._runner import build_viewer


# =============================================================================
# ---- KNOBS ------------------------------------------------------------------
# =============================================================================

# Which trades CSV to visualize. Defaults to the backtester's baseline output.
TRADES_CSV = str(Path(settings.OUTPUT_DIR) / "2_uptrend_reversals_trades.csv")

BAR_SIZE = "2m"

OUTPUT_HTML = str(Path(settings.OUTPUT_DIR) / "trades_viewer.html")

TITLE = "Reversal-long trade viewer"

ATR_SPAN = 14

# Calendar days of prior 2min bars to include on the chart as raw
# context. These extra bars are shown as candles + volume only --
# VWAP / EMA9 / relatr are NOT computed for them (the session's
# indicators still start fresh on session_date, which is what the
# strategy sees). Set to 0 to disable and get only the trade's
# own session as before.
LOOKBACK_DAYS = 3

OPEN_IN_BROWSER = True

# =============================================================================


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )


def main() -> int:
    _configure_logging()

    out = build_viewer(
        trades_csv    = TRADES_CSV,
        output_html   = OUTPUT_HTML,
        bar_size      = BAR_SIZE,
        atr_span      = ATR_SPAN,
        title         = TITLE,
    )

    print("---- VIEWER SUMMARY ----")
    print(f"trades csv  : {TRADES_CSV}")
    print(f"html output : {out}")
    if OPEN_IN_BROWSER:
        try:
            webbrowser.open(Path(out).absolute().as_uri())
        except Exception as e:
            logging.warning("could not auto-open browser: %s", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
