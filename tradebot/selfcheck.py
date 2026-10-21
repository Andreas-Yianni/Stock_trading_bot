"""Αυτοέλεγχος: το bot ξαναελέγχει μόνο του τους κανόνες του με τα πιο πρόσφατα δεδομένα (κάθε μήνα).

1. Backtest των τωρινών κανόνων στη watchlist, χωρισμένο σε παλιά (2/3) και νέα (1/3) δεδομένα.
2. Το ίδιο για λίγες, ΠΡΟΚΑΘΟΡΙΣΜΕΝΕΣ εναλλακτικές (CANDIDATES). Η λίστα είναι μικρή επίτηδες: όσο
   περισσότερες παραλλαγές δοκιμάζεις, τόσο πιο εύκολα κάποια φαίνεται καλή από τύχη.
3. Ένας λογαριασμός για όλη τη watchlist (backtest.portfolio) έναντι του «αγοράζω SPY και κρατάω».
4. Τα πραγματικά αποτελέσματα (ημερολόγιο σημάτων + τα δικά σου trades) έναντι του τι περιμέναμε.

Αλλαγή προτείνεται μόνο αν μια εναλλακτική κερδίζει ΚΑΙ στα παλιά ΚΑΙ στα νέα δεδομένα, με περιθώριο,
και ο χρήστης την εγκρίνει (κουμπί στο Telegram). Το ιστορικό των ελέγχων μένει στο data/selfcheck.json.

Τι μετράμε: R ανά χρόνο ανά μετοχή (όχι μόνο μέσο R ανά trade). Ένα trade +0.5R που κρατάει 3 μήνες
αποδίδει λιγότερο από δύο trades +0.3R του ενός μήνα· το R/χρόνο τα συγκρίνει δίκαια.

Χρήση:  python cli.py --selfcheck
"""
import html
import json
from datetime import datetime, timezone

import pandas as pd

from . import backtest, broker, journal, market, portfolio
from .config import DATA_DIR, DISCLAIMER, load_config, save_config

HISTORY = DATA_DIR / "selfcheck.json"
TIMEFRAME = "1d"

# Εναλλακτικές (αλλαγές στο analysis του config). Οι κανόνες που ισχύουν σήμερα τρέχουν πάντα πρώτοι.
CANDIDATES = {
    "trail5": ("Trailing stop 5 ATR", {"exit_mode": "trend", "trend_atr": 5}),
    "trail6": ("Trailing stop 6 ATR", {"exit_mode": "trend", "trend_atr": 6}),
    "trail8": ("Trailing stop 8 ATR", {"exit_mode": "trend", "trend_atr": 8}),
    "fixed": ("Σταθεροί στόχοι TP1 1.5R / TP2 3R (παλιοί κανόνες)", {"exit_mode": "fixed"}),
    "score20": ("Όριο σκορ 20", {"min_score_for_signal": 20}),
    "score40": ("Όριο σκορ 40", {"min_score_for_signal": 40}),
}
MARGIN = 1.15        # η εναλλακτική πρέπει να βγάζει ≥15% περισσότερα R/χρόνο ...
MIN_GAIN = 0.05      # ... και τουλάχιστον +0.05 R/χρόνο ανά μετοχή, στα παλιά ΚΑΙ στα νέα
MIN_TRADES = 50

# Από το τίμιο τεστ (S&P 500, κανόνες τάσης, trades 2020-2026, 20.000 τυχαίες ακολουθίες): πιθανότητα να είσαι
# μείον μετά από Ν trades ΑΚΟΜΑ ΚΙ ΑΝ η στρατηγική δουλεύει όπως στο backtest (Μ.Ο. +0.19R, τυπ. απόκλιση 3.4R).
LOSS_CHANCE = {10: 50, 30: 41, 50: 37, 100: 28, 200: 20}


def patience_note(n: int) -> str:
    """Τι σημαίνουν (ή δεν σημαίνουν) Ν κλειστά trades: για να μην κρίνουμε τη στρατηγική από την τύχη."""
    if n < 10:
        return f"{n} κλειστά αυτόματα trades: πολύ νωρίς για οποιοδήποτε συμπέρασμα."
    k = max(x for x in LOSS_CHANCE if x <= n)
    return (f"{n} κλειστά αυτόματα trades. Ακόμα κι αν η στρατηγική δουλεύει όπως στο backtest, μετά από {k}+ trades "
            f"υπάρχει ~{LOSS_CHANCE[k]}% πιθανότητα να είσαι μείον. Μέχρι να μαζευτούν εκατοντάδες trades κρίνουμε την "
            f"ΕΚΤΕΛΕΣΗ (📐 διαφορά από το backtest), όχι το αποτέλεσμα.")


def _same(overrides: dict, analysis: dict) -> bool:
    return all(analysis.get(k) == v for k, v in overrides.items())


def _measure(results: list[backtest.Result]) -> dict:
    """R/χρόνο ανά μετοχή, στο σύνολο, στα παλιά και στα νέα δεδομένα."""
    out = {}
    for part in ("all", "old", "new"):
        trades, years = [], 0.0
        for r in results:
            cut = r.split_at
            if part == "all":
                trades += r.trades
            else:
                trades += [t for t in r.trades if (t.opened < cut) == (part == "old")]
            lo, hi = {"all": (r.start, r.end), "old": (r.start, cut), "new": (cut, r.end)}[part]
            years += (hi - lo).days / 365.25
        s = backtest.stats(trades)
        out[part] = {"n": s["n"], "r_year": s.get("total_r", 0.0) / years if years else 0.0,
                     "avg_r": s.get("avg_r", 0.0), "pf": s.get("profit_factor", 0.0), "win": s.get("win_rate", 0.0)}
    return out


def _live() -> dict:
    """Πραγματικά (paper) αποτελέσματα: σήματα του ημερολογίου και τα δικά σου trades."""
    rows = [s for s in journal.all_signals() if s["status"] not in ("OPEN", "SKIP") and s["result_r"] is not None]
    rs = pd.Series([s["result_r"] for s in rows], dtype=float)
    mine, auto = portfolio.stats(), portfolio.stats(broker=True)
    try:
        ex = broker.execution_summary() if broker.configured() else {"n": 0, "avg_diff": None}
    except Exception:
        ex = {"n": 0, "avg_diff": None}
    return {"signals_n": len(rs), "signals_avg": float(rs.mean()) if len(rs) else None,
            "signals_se": float(rs.std() / len(rs) ** 0.5) if len(rs) > 1 else None,
            "mine_n": mine["closed"], "mine_avg": mine.get("avg_r"),
            "auto_n": auto["closed"], "auto_avg": auto.get("avg_r"),
            "exec_n": ex["n"], "exec_avg": ex["avg_diff"]}


def _spy(start: pd.Timestamp, end: pd.Timestamp, days: int) -> dict | None:
    try:
        c = market.fetch_history(market.resolve("SPY.US"), "1d", days + 400)["close"]
    except Exception:
        return None
    c = c[(c.index >= start) & (c.index <= end)]
    if len(c) < 2:
        return None
    years = (c.index[-1] - c.index[0]).days / 365.25
    return {"cagr": 100 * ((c.iloc[-1] / c.iloc[0]) ** (1 / years) - 1), "dd": 100 * float((c / c.cummax() - 1).min())}


def run(symbols: list[str] | None = None, progress=None) -> dict:
    cfg = load_config()
    symbols = symbols or cfg["watchlist"]
    analysis, acc = cfg["analysis"], cfg["account"]
    variants = [("current", "Τωρινοί κανόνες", {})]
    variants += [(k, name, ov) for k, (name, ov) in CANDIDATES.items() if not _same(ov, analysis)]
    rows, port = [], None
    with market.history_cache():
        for n, (key, name, ov) in enumerate(variants):
            results, errors = backtest.run_many(symbols, TIMEFRAME, overrides=ov)
            m = _measure(results)
            rows.append({"key": key, "name": name, "overrides": ov, **m})
            if key == "current" and results:
                p = backtest.portfolio(results, float(acc["balance"]), float(acc["risk_per_trade_pct"]),
                                       float(acc["max_position_pct"]))
                start, end = p["curve"].index[0], p["curve"].index[-1]
                spy = _spy(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1),
                           backtest.DEFAULT_DAYS[TIMEFRAME])
                port = {"cagr": p["cagr"], "dd": p["dd"], "n": p["n"], "years": p["years"], "spy": spy,
                        "errors": [s for s, _ in errors]}
            if progress:
                progress((n + 1) / len(variants))
    cur = rows[0]
    better = [r for r in rows[1:]
              if r["all"]["n"] >= MIN_TRADES
              and all(r[p]["r_year"] >= max(cur[p]["r_year"] * MARGIN, cur[p]["r_year"] + MIN_GAIN)
                      for p in ("old", "new"))]
    best = max(better, key=lambda r: r["all"]["r_year"]) if better else None
    out = {"at": datetime.now(timezone.utc).isoformat(), "symbols": symbols, "timeframe": TIMEFRAME,
           "rows": rows, "portfolio": port, "live": _live(), "suggest": best["key"] if best else None}
    _save(out)
    return out


def _save(out: dict) -> None:
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        hist = []
    hist.append(out)
    HISTORY.write_text(json.dumps(hist[-24:], ensure_ascii=False, indent=1), encoding="utf-8")


def previous() -> dict | None:
    """Ο προηγούμενος αυτοέλεγχος (για σύγκριση με τον τελευταίο)."""
    try:
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return None
    return hist[-2] if len(hist) >= 2 else None


def apply(key: str) -> str:
    """Εφαρμόζει μια εναλλακτική στο config.yaml (μόνο μετά από έγκριση του χρήστη)."""
    name, ov = CANDIDATES[key]
    cfg = load_config()
    cfg["analysis"].update(ov)
    save_config(cfg)
    return name


def _row_line(r: dict, cur: dict | None = None) -> str:
    a, o, n = r["all"], r["old"], r["new"]
    mark = ""
    if cur is not None:
        mark = " ✅" if o["r_year"] > cur["old"]["r_year"] and n["r_year"] > cur["new"]["r_year"] else ""
    return (f"<b>{html.escape(r['name'])}</b>{mark}\n   {a['r_year']:+.2f}R/χρόνο ανά μετοχή · {a['n']} trades · "
            f"Μ.Ο. {a['avg_r']:+.2f}R · κερδισμένα {a['win']:.0f}% · παλιά {o['r_year']:+.2f} / νέα {n['r_year']:+.2f}")


def to_html(out: dict) -> str:
    rows, cur = out["rows"], out["rows"][0]
    lines = [f"<b>🔍 Αυτοέλεγχος του bot</b> · {len(out['symbols'])} μετοχές · {out['timeframe']} · "
             f"{datetime.fromisoformat(out['at']):%d/%m/%Y}", "",
             "<b>1. Κανόνες σε backtest 5 ετών</b> (R/χρόνο ανά μετοχή, με ρίσκο 1R ανά trade)"]
    lines.append(_row_line(cur))
    for r in rows[1:]:
        lines.append(_row_line(r, cur))
    lines.append("<i>✅ = καλύτερο και στα παλιά και στα νέα δεδομένα (χωρίς απαραίτητα να αξίζει αλλαγή).</i>")

    p = out["portfolio"]
    if p:
        lines += ["", "<b>2. Ένας λογαριασμός με όλη τη watchlist</b> (ίδιο κεφάλαιο, ρίσκο και όριο θέσης με το config)",
                  f"Bot: <b>{p['cagr']:+.1f}% τον χρόνο</b> · χειρότερη πτώση {p['dd']:.0f}% · {p['n']} trades "
                  f"σε {p['years']:.1f} χρόνια"]
        if p["spy"]:
            s = p["spy"]
            lines.append(f"SPY (αγοράζω και κρατάω): <b>{s['cagr']:+.1f}% τον χρόνο</b> · χειρότερη πτώση {s['dd']:.0f}%")
            if p["cagr"] < s["cagr"]:
                lines.append("⚠️ Το απλό SPY απέδωσε περισσότερο. Ειλικρινά: σε 17 χρόνια ιστορικού (S&amp;P 500) "
                             "κανένας κανόνας που δοκιμάσαμε δεν το ξεπέρασε σταθερά.")
        lines.append("<i>Προσοχή: η watchlist είναι οι σημερινές πετυχημένες μετοχές, άρα το backtest της είναι "
                     "αισιόδοξο. Στο τίμιο τεστ (όλες οι μετοχές του S&amp;P 500 όσο ήταν στον δείκτη, 2009-2026) "
                     "το bot έβγαλε περίπου 11% τον χρόνο, ενώ το SPY 15%.</i>")
        if p["errors"]:
            lines.append(f"(χωρίς δεδομένα: {', '.join(p['errors'])})")

    lv = out["live"]
    lines += ["", "<b>3. Πραγματικά αποτελέσματα (paper)</b>"]
    if lv["signals_n"]:
        lines.append(f"Σήματα του bot: {lv['signals_n']} κλειστά · Μ.Ο. <b>{lv['signals_avg']:+.2f}R</b> "
                     f"(backtest: {cur['all']['avg_r']:+.2f}R)")
        if lv["signals_n"] >= 30 and lv["signals_se"] and lv["signals_avg"] < cur["all"]["avg_r"] - 2 * lv["signals_se"]:
            lines.append("⚠️ Τα πραγματικά σήματα πάνε <b>πολύ χειρότερα</b> από το backtest: κάτι άλλαξε στην αγορά "
                         "ή το backtest είναι αισιόδοξο. Μείωσε το ρίσκο μέχρι να ξεκαθαρίσει.")
        elif lv["signals_n"] < 30:
            lines.append("<i>Λιγότερα από 30: ακόμα πολύ νωρίς για συμπέρασμα.</i>")
    else:
        lines.append("Κανένα κλειστό σήμα ακόμα.")
    if lv["mine_n"]:
        lines.append(f"Τα δικά σου trades: {lv['mine_n']} κλειστά · Μ.Ο. {lv['mine_avg']:+.2f}R")
    if lv.get("auto_n"):
        lines.append(f"Αυτόματα trades (Alpaca paper): {lv['auto_n']} κλειστά · Μ.Ο. {lv['auto_avg']:+.2f}R")
        lines.append(f"<i>{html.escape(patience_note(lv['auto_n']))}</i>")
    if lv.get("exec_n"):
        if lv["exec_avg"] is None:
            lines.append(f"📐 Εκτέλεση έναντι backtest: {lv['exec_n']} trades με διαφορετική έκβαση από το backtest ⚠️")
        else:
            flag = " ⚠️" if abs(lv["exec_avg"]) > 0.15 else " ✅"
            lines.append(f"📐 Εκτέλεση έναντι backtest: {lv['exec_n']} trades · μέση διαφορά {lv['exec_avg']:+.2f}R{flag}")

    prev = previous()
    if prev and prev.get("rows"):
        pc = prev["rows"][0]["all"]["r_year"]
        lines.append(f"\nΠροηγούμενος έλεγχος ({datetime.fromisoformat(prev['at']):%d/%m}): τωρινοί κανόνες "
                     f"{pc:+.2f} → τώρα {cur['all']['r_year']:+.2f} R/χρόνο ανά μετοχή")

    lines.append("\n<b>📝 Απόφαση</b>")
    if out["suggest"]:
        name = CANDIDATES[out["suggest"]][0]
        lines.append(f"Προτείνω αλλαγή σε: <b>{html.escape(name)}</b>. Είναι καλύτερη και στα παλιά και στα νέα "
                     f"δεδομένα με περιθώριο. Πάτα το κουμπί για να εφαρμοστεί (μπορείς να το αλλάξεις πίσω).")
    else:
        lines.append("Κρατάμε τους τωρινούς κανόνες: καμία εναλλακτική δεν είναι σταθερά καλύτερη "
                     "(και στα παλιά και στα νέα δεδομένα, με περιθώριο).")
    lines.append(f"\n<i>{html.escape(DISCLAIMER)}</i>")
    return "\n".join(lines)
