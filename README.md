# 35_backtesting

Research project for exploring intraday trading ideas end-to-end.
Reads from the local DuckDB cache filled by `34_dataengine` and
uses the shared `indicators` package for per-bar state
(VWAP / EMA / ATR / RVOL / RelATR).

The work is split into four small modules, each one independently
runnable. Each writes plain CSVs (or an HTML page) to `output/` so
runs from different configurations sit side by side for comparison.

## Modules

- **`scanner`** — walks the intraday bars for a universe of symbols
  over a date window and records, per session, the first bar that
  satisfies a configurable filter set. Output: one CSV per run.

- **`backtester`** — takes a scanner CSV as its candidate set,
  replays each session's intraday bars, and simulates one trade
  per (symbol, session) using entry rules and a pluggable exit
  strategy. Output: one trades CSV per run.

- **`viewer`** — turns a trades CSV into a single self-contained
  HTML page (candles, indicator overlays, entry/exit markers) so
  each trade can be reviewed one at a time. No server required.

- **`position_manager`** — reads a trades CSV and simulates an
  account against it: sizing by risk, concurrency cap, commissions,
  cash tracking. Independent of any strategy logic. Output: an
  executions CSV plus an equity-curve CSV.

Each module's `__main__.py` has a `KNOBS` block at the top — edit
those to shape a run, then run it. No CLI flags.

## Requirements

- Python 3.11+
- `34_dataengine`'s DuckDB cache populated for the time window and
  bar size you want to work with
- The sibling `indicators` package checked out at `../indicators`
  next to this project (`pyproject.toml` picks it up as an editable
  local path)
- `uv sync` from this folder installs the rest

## Running

```
cd 35_backtesting
python -m scanner            # -> output/<SCAN_NAME>.csv
python -m backtester         # -> output/<BACKTEST_NAME>_trades.csv
python -m viewer             # -> output/trades_viewer.html
python -m position_manager   # -> output/<RUN_NAME>_*.csv
```

Typical flow: scan → backtest → view the trades → run the account
simulation. Any step can be re-run in isolation with different
knobs; changing one CSV name (via the module's KNOBS block) keeps
old outputs alongside new ones for diffing.

## Layout

```
35_backtesting/
  pyproject.toml
  README.md
  scanner/            trigger-scan module
  backtester/         trade-simulation module
  viewer/             HTML trade viewer
  position_manager/   account simulator
  output/             CSV / HTML outputs (git-ignored)
```

## Design notes

- **Per-bar indicator state comes from the shared `indicators`
  package.** No module reimplements VWAP / RVOL / ATR / RelATR; a
  fix upstream reaches every module on the next run.
- **All I/O reads from the local DuckDB cache.** No network in any
  hot path.
- **CSVs are the interchange format.** Each module reads and/or
  writes plain CSVs so any two runs are trivially comparable and
  the same files feed the next step.
- **Timezone is Helsinki-local** for intraday time-of-day filters
  and viewer axes. Keep `_config.TIMEZONE` in sync across modules
  and with `34_dataengine`.
