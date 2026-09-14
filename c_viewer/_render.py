"""
Build a single self-contained HTML page from a list of trades and
their enriched session candles.

The page loads Plotly.js from a CDN, embeds all the candle/trade
data as a JSON blob, and renders one trade at a time with Prev / Next
navigation (buttons + arrow keys). Timestamps are serialized as
naive ISO strings in Helsinki local time so Plotly's axis labels
show the wall clock the user thinks in.

Each trade view shows:

  * candles + VWAP + EMA9 for the session,
  * blue triangle at the trigger bar's close,
  * exit marker at the exit bar's fill price (colored by exit_reason:
      target -> green, stop -> red, eod -> gray),
  * red dashed horizontal line at the stop level.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd


logger = logging.getLogger(__name__)


def _iso_local(ts) -> str:
    """Serialize a tz-aware pandas Timestamp as a naive Helsinki-local
    ISO string. Plotly renders the axis as wall clock."""
    if ts is None or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    if t.tz is not None:
        t = t.tz_convert("Europe/Helsinki").tz_localize(None)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _clean(x):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return x


def _bars_to_records(bars_df: pd.DataFrame) -> list[dict]:
    out = []
    for row in bars_df.itertuples(index=False):
        out.append({
            "ts":     _iso_local(row.ts),
            "open":   _clean(float(row.open)),
            "high":   _clean(float(row.high)),
            "low":    _clean(float(row.low)),
            "close":  _clean(float(row.close)),
            "volume": _clean(float(row.volume)),
            "vwap":   _clean(row.vwap if row.vwap is None else float(row.vwap)),
            "ema9":   _clean(row.ema9 if row.ema9 is None else float(row.ema9)),
            "relatr": _clean(row.relatr if row.relatr is None else float(row.relatr)),
        })
    return out


def _trade_to_record(row) -> dict:
    sd = row.session_date
    if isinstance(sd, (datetime, pd.Timestamp)):
        sd = pd.Timestamp(sd).date()
    return {
        "session_key":  f"{row.symbol}|{sd.isoformat()}",
        "symbol":       row.symbol,
        "session_date": sd.isoformat(),

        "trigger_ts":     _iso_local(row.trigger_ts),
        "trigger_close":  _clean(float(row.trigger_close)),
        "trigger_ema9":   _clean(float(row.trigger_ema9)),
        "trigger_vwap":   _clean(float(row.trigger_vwap)),
        "trigger_relatr": _clean(float(row.trigger_relatr)),
        "scan_trigger_rvol": _clean(float(row.scan_trigger_rvol)),
        "max_recent_relatr": _clean(float(row.max_recent_relatr)),
        "lookback_low":      _clean(float(row.lookback_low)),

        "entry_ts":     _iso_local(row.entry_ts),
        "entry_price":  _clean(float(row.entry_price)),
        "stop_level":   _clean(float(row.stop_level)),

        "exit_ts":      _iso_local(row.exit_ts),
        "exit_price":   _clean(float(row.exit_price)),
        "exit_reason":  str(row.exit_reason),

        "pnl":       _clean(float(row.pnl)),
        "pnl_pct":   _clean(float(row.pnl_pct)),
        "bars_held": int(row.bars_held) if not pd.isna(row.bars_held) else None,
    }


def build_payload(
    trades_df: pd.DataFrame,
    sessions_bars: dict[tuple[str, date], pd.DataFrame],
) -> dict:
    sessions_out: dict[str, dict] = {}
    for (sym, sd), bars in sessions_bars.items():
        key = f"{sym}|{sd.isoformat()}"
        sessions_out[key] = {
            "symbol":       sym,
            "session_date": sd.isoformat(),
            "bars":         _bars_to_records(bars),
        }
    trades_out = [_trade_to_record(r) for r in trades_df.itertuples(index=False)]
    return {"sessions": sessions_out, "trades": trades_out}


_HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>__TITLE__</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  html, body { margin: 0; padding: 0; background: #0e1116; color: #e6e6e6;
               font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI",
                     Roboto, Helvetica, Arial, sans-serif; }
  header { display: flex; align-items: center; gap: 12px;
           padding: 10px 14px; background: #171b22;
           border-bottom: 1px solid #262c36; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; color: #f6f6f6; }
  header .spacer { flex: 1 1 auto; }
  button { background: #1f2632; color: #e6e6e6; border: 1px solid #313a49;
           padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  button:hover { background: #2a3140; }
  button:disabled { opacity: 0.4; cursor: default; }
  #counter { font-variant-numeric: tabular-nums; color: #a9b1bd; }
  #search { background: #1f2632; color: #e6e6e6; border: 1px solid #313a49;
            padding: 5px 10px; border-radius: 6px; font-size: 13px;
            width: 120px; font-family: inherit; }
  #search::placeholder { color: #7a8395; }
  #search:focus { outline: none; border-color: #38bdf8; }
  #search.nomatch { border-color: #ef4444; }
  #subtitle { padding: 8px 14px; background: #131820;
              border-bottom: 1px solid #1e242e; font-size: 13px; color: #cfd6e0; }
  #subtitle b { color: #f6f6f6; }
  .badge { display:inline-block; padding: 1px 8px; border-radius: 999px;
           font-size: 11.5px; font-weight: 600; margin-left: 6px;
           border: 1px solid transparent; }
  .badge.target { background: rgba(34,197,94,0.15);  color: #22c55e; border-color:#22c55e33; }
  .badge.stop   { background: rgba(239,68,68,0.15);  color: #ef4444; border-color:#ef444433; }
  .badge.eod    { background: rgba(156,163,175,0.15); color: #9ca3af; border-color:#9ca3af33; }
  .pnl.pos { color: #22c55e; }
  .pnl.neg { color: #ef4444; }
  #chart { height: calc(100vh - 190px); }
  #info { padding: 8px 14px; background: #131820; border-top: 1px solid #1e242e;
          font-size: 12.5px; color: #a9b1bd; display: flex; flex-wrap: wrap; gap: 18px; }
  #info span { color: #e6e6e6; font-variant-numeric: tabular-nums; }
  kbd { background: #262c36; padding: 1px 6px; border-radius: 4px;
        font: 11px ui-monospace, SFMono-Regular, Menlo, monospace; color: #f6f6f6; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <button id="prev">◀ Prev</button>
  <button id="next">Next ▶</button>
  <span id="counter">–</span>
  <input id="search" type="text" placeholder="ticker…" spellcheck="false"
         autocomplete="off"/>
  <span class="spacer"></span>
  <span style="color:#7a8395; font-size:12px;">
    Nav: <kbd>←</kbd> <kbd>→</kbd> &nbsp; Search: <kbd>/</kbd>
  </span>
</header>
<div id="subtitle">–</div>
<div id="chart"></div>
<div id="info">–</div>

<script>
const DATA = __PAYLOAD__;

const N = DATA.trades.length;

// ---- Filtering by ticker ------------------------------------------
// `visible` holds indices into DATA.trades that pass the current
// ticker filter. `pos` is where we are inside `visible`. When the
// search box is empty, visible = [0, 1, ..., N-1] so navigation
// walks every trade.
let visible = [];
let pos = 0;

function applyFilter(q) {
  const query = (q || "").trim().toUpperCase();
  const cur = visible[pos];   // remember which trade we were on
  if (query === "") {
    visible = DATA.trades.map((_, i) => i);
  } else {
    visible = [];
    for (let i = 0; i < N; i++) {
      if (DATA.trades[i].symbol.toUpperCase().includes(query)) {
        visible.push(i);
      }
    }
  }
  // Try to stay on the same trade if it survived the filter;
  // otherwise land on the first match.
  const stay = visible.indexOf(cur);
  pos = stay >= 0 ? stay : 0;
  document.getElementById("search").classList.toggle(
    "nomatch", query !== "" && visible.length === 0,
  );
  render();
}

function fmt(x, dp) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return Number(x).toFixed(dp === undefined ? 2 : dp);
}
function pct(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  return (Number(x) * 100).toFixed(2) + "%";
}

// Colors per exit reason -- keep marker + badge in sync.
const EXIT_COLORS = {
  target: "#22c55e",
  stop:   "#ef4444",
  eod:    "#9ca3af",
};

function render() {
  if (N === 0) {
    document.getElementById("subtitle").innerHTML = "<b>No trades in this CSV.</b>";
    document.getElementById("counter").textContent = "0 of 0";
    Plotly.purge("chart");
    return;
  }
  if (visible.length === 0) {
    // Filter matched nothing -- keep last-drawn chart, but tell the
    // user the counter is at 0/N and disable navigation.
    document.getElementById("counter").textContent = `0 of ${N}`;
    document.getElementById("prev").disabled = true;
    document.getElementById("next").disabled = true;
    document.getElementById("subtitle").innerHTML =
      "<b>No trades match this ticker filter.</b>";
    return;
  }
  const idx = visible[pos];
  const t = DATA.trades[idx];
  const s = DATA.sessions[t.session_key];

  const total = visible.length === N
    ? `${pos + 1} of ${N}`
    : `${pos + 1} of ${visible.length}  (filtered from ${N})`;
  document.getElementById("counter").textContent = total;
  document.getElementById("prev").disabled = (pos === 0);
  document.getElementById("next").disabled = (pos === visible.length - 1);

  const pnlClass = t.pnl_pct >= 0 ? "pos" : "neg";
  document.getElementById("subtitle").innerHTML =
    `<b>${t.symbol}</b> &nbsp; ${t.session_date} &nbsp;·&nbsp; ` +
    `trigger @ <b>${t.trigger_ts.slice(11,16)}</b> · ` +
    `entry @ <b>${t.entry_ts.slice(11,16)}</b> ${fmt(t.entry_price)} · ` +
    `exit @ <b>${t.exit_ts.slice(11,16)}</b> ${fmt(t.exit_price)}` +
    `<span class="badge ${t.exit_reason}">${t.exit_reason}</span>` +
    `<span class="spacer"></span> &nbsp;·&nbsp; ` +
    `pnl <span class="pnl ${pnlClass}">${pct(t.pnl_pct)}</span> · ` +
    `${t.bars_held} bars`;

  const bars = s.bars;
  const xs = bars.map(b => b.ts);

  const priceCandles = {
    type: "candlestick",
    x: xs,
    open: bars.map(b => b.open), high: bars.map(b => b.high),
    low:  bars.map(b => b.low),  close: bars.map(b => b.close),
    name: t.symbol,
    increasing: {line: {color: "#22c55e"}, fillcolor: "#22c55e"},
    decreasing: {line: {color: "#ef4444"}, fillcolor: "#ef4444"},
    xaxis: "x", yaxis: "y",
    hoverinfo: "x+y",
  };
  const vwapLine = {
    type: "scatter", mode: "lines", x: xs, y: bars.map(b => b.vwap),
    name: "VWAP", line: {color: "#ef4444", width: 1.2},
    xaxis: "x", yaxis: "y",
    hovertemplate: "VWAP %{y:.2f}<extra></extra>",
  };
  const ema9Line = {
    type: "scatter", mode: "lines", x: xs, y: bars.map(b => b.ema9),
    name: "EMA9", line: {color: "#3b82f6", width: 1.2},
    xaxis: "x", yaxis: "y",
    hovertemplate: "EMA9 %{y:.2f}<extra></extra>",
  };
  // Blue entry marker sits on the FILL bar (bar N+1) at the fill price,
  // not on the signal / trigger candle. That's where the position
  // actually opened.
  const entryMarker = {
    type: "scatter", mode: "markers",
    x: [t.entry_ts], y: [t.entry_price],
    name: "entry",
    marker: {symbol: "triangle-up", color: "#38bdf8", size: 14,
             line: {color: "#0e1116", width: 1}},
    xaxis: "x", yaxis: "y",
    hovertemplate: "Entry %{y:.2f}<extra></extra>",
  };
  const exitColor = EXIT_COLORS[t.exit_reason] || "#e6e6e6";
  const exitMarker = {
    type: "scatter", mode: "markers",
    x: [t.exit_ts], y: [t.exit_price],
    name: `exit (${t.exit_reason})`,
    marker: {symbol: "x", color: exitColor, size: 14,
             line: {color: "#0e1116", width: 1}},
    xaxis: "x", yaxis: "y",
    hovertemplate: `Exit %{y:.2f} (${t.exit_reason})<extra></extra>`,
  };
  const volBars = {
    type: "bar", x: xs, y: bars.map(b => b.volume), name: "vol",
    marker: {color: "#334155"}, xaxis: "x", yaxis: "y2",
    hovertemplate: "%{y:,.0f}<extra></extra>",
  };

  // Auto-frame around the trade: from a few bars before the trigger
  // to a few bars after the exit, unless the whole session is
  // narrower.
  const triggerIdx = xs.indexOf(t.trigger_ts);
  const exitIdx    = xs.indexOf(t.exit_ts);
  let xRange = null;
  if (triggerIdx >= 0 && exitIdx >= 0) {
    const lo = Math.max(0, triggerIdx - 25);
    const hi = Math.min(xs.length - 1, exitIdx + 20);
    xRange = [xs[lo], xs[hi]];
  }

  const layout = {
    paper_bgcolor: "#0e1116",
    plot_bgcolor:  "#0e1116",
    font: {color: "#cfd6e0"},
    margin: {l: 60, r: 30, t: 20, b: 40},
    dragmode: "pan",
    showlegend: true,
    legend: {orientation: "h", y: 1.08, x: 0, bgcolor: "rgba(0,0,0,0)"},
    xaxis: {
      type: "date", rangeslider: {visible: false},
      gridcolor: "#1e242e", zerolinecolor: "#1e242e",
      range: xRange,
    },
    yaxis:  {domain: [0.28, 1.0], gridcolor: "#1e242e",
             zerolinecolor: "#1e242e", tickformat: ".2f", title: "Price"},
    yaxis2: {domain: [0.0,  0.22], gridcolor: "#1e242e",
             zerolinecolor: "#1e242e", title: "Volume", showgrid: false},
    shapes: [
      // stop-level line (red dashed) across the price axis
      {type: "line", xref: "x", yref: "y", x0: xs[0], x1: xs[xs.length-1],
       y0: t.stop_level, y1: t.stop_level,
       line: {color: "#ef4444", width: 1.2, dash: "dash"}},
    ],
    annotations: [
      {xref: "paper", yref: "y", x: 1.0, y: t.stop_level,
       xanchor: "left", yanchor: "middle", showarrow: false,
       text: ` stop ${fmt(t.stop_level)}`, font: {color: "#ef4444", size: 11}},
    ],
  };

  Plotly.react("chart",
    [priceCandles, vwapLine, ema9Line, entryMarker, exitMarker, volBars],
    layout,
    {responsive: true, displaylogo: false, scrollZoom: true,
     modeBarButtonsToRemove: ["select2d", "lasso2d"]});

  const risk = t.entry_price - t.stop_level;
  document.getElementById("info").innerHTML =
    `entry <span>${fmt(t.entry_price)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `stop <span>${fmt(t.stop_level)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `risk <span>${fmt(risk)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `exit <span>${fmt(t.exit_price)}</span> (${t.exit_reason})` +
    `<span style="color:#7a8395">·</span>` +
    `pnl <span class="pnl ${pnlClass}">${pct(t.pnl_pct)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `bars_held <span>${t.bars_held}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `trigger_vwap <span>${fmt(t.trigger_vwap)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `trigger_relatr <span>${fmt(t.trigger_relatr)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `scan_trigger_rvol <span>${fmt(t.scan_trigger_rvol)}</span>` +
    `<span style="color:#7a8395">·</span>` +
    `max_recent_relatr <span>${fmt(t.max_recent_relatr)}</span>`;
}

document.getElementById("prev").addEventListener("click",
  () => { if (pos > 0)                    { pos--; render(); } });
document.getElementById("next").addEventListener("click",
  () => { if (pos < visible.length - 1)   { pos++; render(); } });

// Arrow-key nav walks the FILTERED list. Suppress it while the
// search box has focus so the user can move the caret with arrows.
document.addEventListener("keydown", (ev) => {
  if (document.activeElement && document.activeElement.id === "search") return;
  if (ev.key === "ArrowLeft"  && pos > 0)                  { pos--; render(); }
  if (ev.key === "ArrowRight" && pos < visible.length - 1) { pos++; render(); }
});

// Ticker search -- filters live as the user types. `/` from anywhere
// focuses the box, Esc clears + blurs it. Enter is a no-op (nothing
// to submit).
const searchEl = document.getElementById("search");
searchEl.addEventListener("input", (ev) => applyFilter(ev.target.value));
searchEl.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") { searchEl.value = ""; applyFilter(""); searchEl.blur(); }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "/" && document.activeElement !== searchEl) {
    ev.preventDefault();
    searchEl.focus();
    searchEl.select();
  }
});

applyFilter("");   // seeds visible = [0..N-1] and calls render()
</script>
</body>
</html>
"""


def build_html(*, title: str, payload: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = (_HTML_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False,
                                               allow_nan=False, default=str)))
    output_path.write_text(html, encoding="utf-8")
    logger.info("wrote %s (%d trades, %d sessions)",
                output_path, len(payload["trades"]), len(payload["sessions"]))
    return output_path
