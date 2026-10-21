"""Replay του backtest «σαν βίντεο» για το dashboard: τα κεριά εμφανίζονται ένα-ένα με τα trades.

Φτιάχνει μια αυτόνομη σελίδα HTML (Plotly.js) που μπαίνει στο Streamlit με components.html,
ώστε το play/pause να τρέχει στον browser χωρίς να ξανατρέχει το dashboard.
"""
import json

from .market import TF_DELTA

PLOTLY_JS = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"


def _data(r) -> dict:
    df = r.df
    bar = TF_DELTA[r.timeframe]
    fmt = "%Y-%m-%d" if bar >= TF_DELTA["1d"] else "%Y-%m-%d %H:%M"
    trades = []
    for t in r.trades:
        entry_i = int(df.index.searchsorted(t.opened - bar))
        exit_i = max(entry_i, int(df.index.searchsorted(t.exit_at, side="right")) - 1)
        trades.append({
            "ei": entry_i, "xi": exit_i, "side": t.side, "score": round(t.score),
            "entry": t.entry, "sl": t.stop_loss, "tp1": t.tp1, "tp2": t.tp2,
            "exit": t.exit_price, "status": t.status, "r": round(t.r, 2), "reasons": t.reasons,
            "gain": t.r * t.risk_frac,   # μεταβολή κεφαλαίου (ποσοστό) όταν κλείνει
        })
    # Κεφάλαιο σε κάθε κερί: αλλάζει στο κερί όπου κλείνει κάθε trade
    equity, val, k = [], r.balance, 0
    by_exit = sorted(trades, key=lambda x: x["xi"])
    for i in range(len(df)):
        while k < len(by_exit) and by_exit[k]["xi"] <= i:
            val *= 1 + by_exit[k]["gain"]
            k += 1
        equity.append(round(val, 2))
    return {
        "symbol": r.asset.symbol, "tf": r.timeframe, "balance": r.balance,
        "t": [ts.strftime(fmt) for ts in df.index.tz_convert(None)],
        "o": df["open"].round(6).tolist(), "h": df["high"].round(6).tolist(),
        "l": df["low"].round(6).tolist(), "c": df["close"].round(6).tolist(),
        "e20": df["ema20"].round(6).tolist(), "e50": df["ema50"].round(6).tolist(),
        "eq": equity, "bh": [round(x, 2) for x in r.buy_hold.tolist()],
        "trades": trades,
    }


def html(r) -> str:
    return (TEMPLATE.replace("__PLOTLY__", PLOTLY_JS)
            .replace("__DATA__", json.dumps(_data(r), ensure_ascii=False).replace("</", "<\\/")))


TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<script src="__PLOTLY__"></script>
<style>
  :root { --fg:#1f2d3d; --muted:#6b7a89; --line:#dfe4ea; --bg:#ffffff; --panel:#f6f8fa;
          --win:#27ae60; --loss:#e74c3c; --long:#2980b9; --short:#e67e22; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--fg); background:var(--bg); }
  .bar { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:6px 2px; }
  button, select, input { font: inherit; font-size:14px; padding:5px 9px; border:1px solid var(--line);
          border-radius:6px; background:#fff; color:var(--fg); cursor:pointer; }
  button.main { background:var(--long); color:#fff; border-color:var(--long); min-width:92px; }
  label { font-size:13px; color:var(--muted); display:flex; align-items:center; gap:4px; }
  #scrub { flex:1; min-width:160px; }
  .wrap { display:flex; gap:10px; }
  #chart { flex:1; min-width:0; height:640px; }
  #info { width:300px; flex:none; background:var(--panel); border-radius:8px; padding:10px 12px;
          font-size:13px; line-height:1.45; height:620px; overflow:auto; }
  #info h4 { margin:10px 0 4px; font-size:13px; }
  .big { font-size:20px; font-weight:600; }
  .win { color:var(--win); } .loss { color:var(--loss); } .muted { color:var(--muted); }
  .reason { margin:1px 0; }
  @media (max-width: 800px) { .wrap { flex-direction:column; } #info { width:auto; height:auto; } }
</style></head>
<body>
<div class="bar">
  <button id="play" class="main">▶️ Play</button>
  <button id="prevT" title="Προηγούμενο trade">⏮ Trade</button>
  <button id="back" title="Ένα κερί πίσω">◀</button>
  <button id="fwd" title="Ένα κερί μπροστά">▶</button>
  <button id="nextT" title="Επόμενο trade">Trade ⏭</button>
  <label>Ταχύτητα
    <select id="speed">
      <option value="1">1 κερί/δευτ.</option><option value="3">3 κεριά/δευτ.</option>
      <option value="10" selected>10 κεριά/δευτ.</option><option value="30">30 κεριά/δευτ.</option>
    </select></label>
  <label><input type="checkbox" id="pauseNew" checked> Παύση σε νέο trade</label>
  <label>Ημερομηνία <input type="date" id="date"></label>
  <button id="go">Πήγαινε</button>
</div>
<div class="bar"><input type="range" id="scrub" min="0" value="0"><span id="pos" class="muted"></span></div>
<div class="wrap"><div id="chart"></div><div id="info"></div></div>
<script>
const D = __DATA__;
const N = D.t.length, W = 150;
const tr = D.trades;
let idx = Math.min(N - 1, tr.length ? Math.max(W - 1, tr[0].ei + 20) : W - 1);
let playing = false, timer = null;
const $ = id => document.getElementById(id);
$("scrub").max = N - 1;
const fmt = x => x >= 1 ? x.toLocaleString("el-GR", {maximumFractionDigits: 2}) : x.toPrecision(5);

function render() {
  const lo = Math.max(0, idx - W + 1), x = D.t.slice(lo, idx + 1);
  const sl = a => a.slice(lo, idx + 1);
  const traces = [
    {type: "candlestick", x, open: sl(D.o), high: sl(D.h), low: sl(D.l), close: sl(D.c), name: "Τιμή",
     increasing: {line: {color: "#26a69a"}}, decreasing: {line: {color: "#ef5350"}}, hoverinfo: "x+y"},
    {type: "scatter", mode: "lines", x, y: sl(D.e20), name: "EMA20", line: {color: "#2b8cbe", width: 1}, hoverinfo: "skip"},
    {type: "scatter", mode: "lines", x, y: sl(D.e50), name: "EMA50", line: {color: "#f39c12", width: 1}, hoverinfo: "skip"},
  ];
  const shapes = [], ann = [];
  const visible = tr.filter(t => t.ei <= idx && t.xi >= lo);
  for (const t of visible) {
    const closed = t.xi <= idx, col = t.r > 0 ? "#27ae60" : "#e74c3c";
    const x0 = D.t[Math.max(t.ei, lo)], x1 = D.t[Math.min(closed ? t.xi : idx, idx)];
    if (t.ei >= lo) traces.push({type: "scatter", mode: "markers", x: [D.t[t.ei]], y: [t.entry], showlegend: false,
      marker: {symbol: t.side === "LONG" ? "triangle-up" : "triangle-down", size: 14,
               color: t.side === "LONG" ? "#2980b9" : "#e67e22", line: {color: "#fff", width: 1}},
      hovertext: `${t.side} · σκορ ${t.score} · είσοδος ${fmt(t.entry)}`, hoverinfo: "text"});
    if (closed) {
      traces.push({type: "scatter", mode: "lines+markers", x: [D.t[t.ei >= lo ? t.ei : lo], D.t[t.xi]],
        y: [t.entry, t.exit], showlegend: false, line: {color: col, width: 1, dash: "dot"},
        marker: {size: [0, 11], color: col, line: {color: "#fff", width: 1}},
        hovertext: ["", `${t.status} · ${t.r > 0 ? "+" : ""}${t.r}R`], hoverinfo: "text"});
    } else {
      for (const [y, c, name, dash] of [[t.entry, "#2980b9", "Είσοδος", "dash"], [t.sl, "#e74c3c", "SL", "solid"],
                                        [t.tp1, "#27ae60", "TP1", "solid"], [t.tp2, "#16a085", "TP2", "solid"]]) {
        if (y === null) continue;
        shapes.push({type: "line", xref: "x", yref: "y", x0, x1, y0: y, y1: y, line: {color: c, width: 1.5, dash}});
        ann.push({xref: "x", yref: "y", x: x1, y, text: `${name} ${fmt(y)}`, showarrow: false, xanchor: "left",
                  font: {size: 11, color: c}, bgcolor: "rgba(255,255,255,0.8)"});
      }
    }
  }
  const ex = D.t.slice(0, idx + 1);
  traces.push({type: "scatter", mode: "lines", x: ex, y: D.eq.slice(0, idx + 1), name: "Κεφάλαιο στρατηγικής",
               xaxis: "x2", yaxis: "y2", line: {color: "#2980b9", width: 2, shape: "hv"}});
  traces.push({type: "scatter", mode: "lines", x: ex, y: D.bh.slice(0, idx + 1), name: "Buy & hold",
               xaxis: "x2", yaxis: "y2", line: {color: "#95a5a6", width: 1}});
  Plotly.react("chart", traces, {
    margin: {l: 55, r: 90, t: 10, b: 30}, showlegend: true, legend: {orientation: "h", y: 1.04, x: 0},
    xaxis: {type: "category", rangeslider: {visible: false}, nticks: 6, tickangle: 0, anchor: "y"},
    yaxis: {domain: [0.36, 1], title: D.symbol}, shapes, annotations: ann,
    xaxis2: {type: "category", nticks: 6, tickangle: 0, anchor: "y2"},
    yaxis2: {domain: [0, 0.26], title: "Κεφάλαιο"},
    paper_bgcolor: "#fff", plot_bgcolor: "#fff",
  }, {displayModeBar: false, responsive: true});
  info();
  $("scrub").value = idx;
  $("pos").textContent = `${idx + 1} / ${N} κεριά`;
}

function info() {
  const done = tr.filter(t => t.xi <= idx), active = tr.find(t => t.ei <= idx && t.xi > idx);
  const wins = done.filter(t => t.r > 0).length, totalR = done.reduce((s, t) => s + t.r, 0);
  const eq = D.eq[idx], pct = (eq / D.balance - 1) * 100, bh = (D.bh[idx] / D.balance - 1) * 100;
  const cls = v => v > 0 ? "win" : v < 0 ? "loss" : "";
  let h = `<div class="muted">${D.symbol} · ${D.tf}</div><div class="big">${D.t[idx]}</div>
    <div>Τιμή: <b>${fmt(D.c[idx])}</b></div>
    <h4>💰 Κεφάλαιο</h4><div class="big ${cls(pct)}">${fmt(eq)} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</div>
    <div class="muted">Buy &amp; hold: ${bh >= 0 ? "+" : ""}${bh.toFixed(1)}%</div>
    <h4>📊 Μέχρι τώρα</h4><div>${done.length} trades · ${wins} κέρδη · ${done.length - wins} ζημιές</div>
    <div>Σύνολο: <b class="${cls(totalR)}">${totalR >= 0 ? "+" : ""}${totalR.toFixed(1)}R</b></div>`;
  if (active) {
    h += `<h4>🎯 Ανοιχτό trade: ${active.side} (σκορ ${active.score})</h4>
      <div>Είσοδος ${fmt(active.entry)} · SL ${fmt(active.sl)}<br>${active.tp1 === null ? "Χωρίς TP: trailing stop" : `TP1 ${fmt(active.tp1)} · TP2 ${fmt(active.tp2)}`}</div>
      <h4>Γιατί μπήκε</h4><div id="reasons"></div>`;
  } else {
    h += `<h4>⏳ Καμία ανοιχτή θέση</h4><div class="muted">Το bot περιμένει σήμα με |σκορ| πάνω από το όριο.</div>`;
  }
  const last = done[done.length - 1];
  if (last) h += `<h4>Τελευταίο κλειστό</h4><div>${last.side} → ${last.status}:
      <b class="${cls(last.r)}">${last.r > 0 ? "+" : ""}${last.r}R</b></div>`;
  $("info").innerHTML = h;
  if (active) for (const r of active.reasons) {
    const d = document.createElement("div"); d.className = "reason"; d.textContent = r; $("reasons").appendChild(d);
  }
}

function step() {
  if (idx >= N - 1) return stop();
  idx++;
  render();
  if ($("pauseNew").checked && tr.some(t => t.ei === idx)) stop();
}
function start() { playing = true; $("play").textContent = "⏸ Παύση"; timer = setInterval(step, 1000 / +$("speed").value); }
function stop() { playing = false; $("play").textContent = "▶️ Play"; clearInterval(timer); }
function jump(i) { idx = Math.max(0, Math.min(N - 1, i)); render(); }

$("play").onclick = () => playing ? stop() : start();
$("speed").onchange = () => { if (playing) { stop(); start(); } };
$("back").onclick = () => { stop(); jump(idx - 1); };
$("fwd").onclick = () => { stop(); jump(idx + 1); };
$("nextT").onclick = () => { stop(); const t = tr.find(t => t.ei > idx); if (t) jump(t.ei); };
$("prevT").onclick = () => { stop(); const t = [...tr].reverse().find(t => t.ei < idx); if (t) jump(t.ei); };
$("scrub").oninput = e => { stop(); jump(+e.target.value); };
$("go").onclick = () => { const d = $("date").value; if (!d) return; stop();
  const i = D.t.findIndex(t => t >= d); jump(i < 0 ? N - 1 : i); };
document.addEventListener("keydown", e => { if (e.code === "Space" && e.target.tagName !== "INPUT") {
  e.preventDefault(); playing ? stop() : start(); } });
$("date").min = D.t[0].slice(0, 10); $("date").max = D.t[N - 1].slice(0, 10);
render();
</script></body></html>
"""
