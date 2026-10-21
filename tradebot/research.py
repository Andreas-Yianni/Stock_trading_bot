"""«Εργαστήριο»: μετράει αν κάθε δείκτης / pattern προβλέπει την επόμενη κίνηση της τιμής.

Για κάθε κερί υπολογίζουμε την κίνηση των επόμενων H κεριών σε μονάδες ATR. Αφαιρούμε τη μέση
κίνηση του συμβόλου (αλλιώς σε ανοδική αγορά κάθε «αγόρασε» φαίνεται καλό) και κοιτάμε αν, όταν
το σήμα λέει πάνω/κάτω, η τιμή όντως πήγε προς τα εκεί περισσότερο απ' ό,τι συνήθως.
Τα αποτελέσματα χωρίζονται σε παλιά (2/3) και νέα (1/3) δεδομένα: ένα σήμα αξίζει μόνο αν
δουλεύει και στα δύο.

Χρήση:  python -m tradebot.research 4h
"""
import sys

import pandas as pd

from . import backtest, engine, indicators, market, patterns
from .config import DATA_DIR

CRYPTO = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "LTC"]
STOCKS = ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "AMD", "JPM", "SPY"]
HORIZON = {"4h": 12, "1d": 5, "1w": 4}   # 4h: 2 μέρες, 1d: μία εβδομάδα


def signals(symbol: str, tf: str, days: int | None = None) -> pd.DataFrame:
    """Ένα DataFrame ανά κερί: μελλοντική κίνηση + όλα τα σήματα (factors και patterns)."""
    asset = market.resolve(symbol)
    days = days or backtest.DEFAULT_DAYS[tf]
    bar = market.TF_DELTA[tf]
    warm = int(backtest.WARMUP_BARS * bar / pd.Timedelta(days=1)) + 1
    df = patterns.add_all(indicators.add_all(market.fetch_history(asset, tf, days + warm)))

    htf_name = market.HIGHER_TF[tf]
    htf = htf_close = None
    if htf_name != tf:
        h_days = days + warm + int(backtest.HTF_WARMUP_BARS * market.TF_DELTA[htf_name] / pd.Timedelta(days=1))
        htf = indicators.add_all(market.fetch_history(asset, htf_name, h_days))
        htf_close = htf.index + market.TF_DELTA[htf_name]

    rows = []
    close_times = df.index + bar
    for i in range(backtest.WARMUP_BARS, len(df)):
        h = htf.iloc[: int(htf_close.searchsorted(close_times[i], side="right"))] if htf is not None else None
        fs = engine._trend_factors(df.iloc[: i + 1], h, htf_name)
        row = {f"f_{f.name}": f.score for f in fs}
        row["f_score"] = 100 * sum(f.score * f.weight for f in fs) / sum(f.weight for f in fs)
        rows.append(row)
    out = pd.DataFrame(rows, index=df.index[backtest.WARMUP_BARS:])

    d = df.iloc[backtest.WARMUP_BARS:]
    H = HORIZON[tf]
    fwd = (d["close"].shift(-H) - d["close"]) / d["atr"]
    out["fwd"] = fwd
    out["adj"] = fwd - fwd.mean()               # χωρίς τη «φυσική» τάση του συμβόλου
    for col in patterns.NAMES:
        out[col] = d[col]
    out["symbol"] = asset.symbol
    out["kind"] = asset.kind
    cut = backtest.split_at(out.index[0], out.index[-1])
    out["new"] = out.index >= cut
    return out.dropna(subset=["fwd"])


def _row(name: str, sub: pd.DataFrame, direction: int, horizon: int) -> dict:
    s = sub["adj"] * direction
    # Τα διαδοχικά κεριά μοιράζονται την ίδια μελλοντική κίνηση: ~n/H ανεξάρτητες μετρήσεις
    eff_n = max(len(s) / horizon, 1)
    t = s.mean() / (s.std() / eff_n ** 0.5) if len(s) > 1 and s.std() > 0 else 0.0
    per_sym = s.groupby(sub["symbol"]).mean()
    old, new = s[~sub["new"]], s[sub["new"]]
    return {
        "σήμα": name, "n": len(s),
        "hit%": round(100 * ((sub["fwd"] * direction) > 0).mean(), 1),
        "edge": round(s.mean(), 3),
        "t": round(t, 1),
        "edge_old": round(old.mean(), 3) if len(old) else None,
        "edge_new": round(new.mean(), 3) if len(new) else None,
        "σύμβολα+": f"{(per_sym > 0).sum()}/{len(per_sym)}",
    }


def study(data: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Πίνακας: για κάθε σήμα και κατεύθυνση, πόσο καλύτερα από το συνηθισμένο κινήθηκε η τιμή (σε ATR)."""
    rows = []
    for col in [c for c in data if c.startswith("f_") and c != "f_score"]:
        for v in sorted(data[col].dropna().unique()):
            if v == 0:
                continue
            sub = data[data[col] == v]
            rows.append(_row(f"{col[2:]} = {v:+.2g}", sub, 1 if v > 0 else -1, horizon))
    for lo, hi in [(30, 40), (40, 60), (60, 101)]:
        for sign in (1, -1):
            sub = data[(data["f_score"] * sign >= lo) & (data["f_score"] * sign < hi)]
            rows.append(_row(f"σκορ {'+' if sign > 0 else '-'}{lo}..{hi - 1}", sub, sign, horizon))
    for col, (name, *_rest) in patterns.NAMES.items():
        for sign in (1, -1):
            sub = data[data[col] == sign]
            rows.append(_row(f"{name} {'▲' if sign > 0 else '▼'}", sub, sign, horizon))
    return pd.DataFrame(rows)


def run(tf: str, symbols: list[str] | None = None) -> pd.DataFrame:
    parts = []
    for sym in symbols or CRYPTO + STOCKS:
        try:
            parts.append(signals(sym, tf))
            print(f"  {sym}: {len(parts[-1])} κεριά", file=sys.stderr)
        except Exception as e:
            print(f"  {sym}: σφάλμα {e}", file=sys.stderr)
    return pd.concat(parts)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tf = sys.argv[1] if len(sys.argv) > 1 else "4h"
    data = run(tf)
    data.to_pickle(DATA_DIR / f"research_{tf}.pkl")
    pd.set_option("display.width", 200, "display.max_rows", 200)
    for label, sub in [("ΟΛΑ", data), ("CRYPTO", data[data.kind == "crypto"]), ("ΜΕΤΟΧΕΣ", data[data.kind == "stock"])]:
        print(f"\n===== {tf} · {label} · {len(sub)} κεριά · ορίζοντας {HORIZON[tf]} κεριά =====")
        print(study(sub, HORIZON[tf]).to_string(index=False))
