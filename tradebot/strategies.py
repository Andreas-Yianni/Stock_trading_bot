"""Γνωστές στρατηγικές με δημοσιευμένους κανόνες, για σύγκριση με το bot (έρευνα, όχι live σήματα).

Κάθε στρατηγική δίνει: σήμα εισόδου στο κλείσιμο του κεριού (+1 LONG / -1 SHORT), αρχικό stop
(σε ATR) και κανόνα εξόδου που ελέγχεται στο κλείσιμο. Το stop ελέγχεται μέσα στο κερί· αν η τιμή
ανοίξει πέρα από το stop (gap), η έξοδος γίνεται στο άνοιγμα (χειρότερη τιμή). Ένα trade τη φορά,
με τις ίδιες προμήθειες με το backtest του bot, και αποτέλεσμα σε R (1R = το αρχικό ρίσκο).

Χρήση:  python -m tradebot.strategies 1d
"""
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import backtest, indicators, market, research
from .config import load_config


@dataclass
class Strategy:
    name: str
    description: str
    stop_atr: float
    long_only: bool = False


def _rsi2(c: pd.Series) -> pd.Series:
    return indicators.rsi(c, 2)


def entries(name: str, df: pd.DataFrame, tf: str) -> pd.Series:
    """+1 / -1 / 0 στο κλείσιμο κάθε κεριού."""
    c, h, l = df["close"], df["high"], df["low"]
    if name == "turtle":
        up = c > h.rolling(20).max().shift()
        dn = c < l.rolling(20).min().shift()
        return pd.Series(np.where(up, 1, np.where(dn, -1, 0)), index=df.index)
    if name == "rsi2":
        sma200 = c.rolling(200).mean()
        return pd.Series(np.where((c > sma200) & (_rsi2(c) < 10), 1, 0), index=df.index)
    if name == "tsmom":
        n = 90 if tf == "1d" else 540            # ~3 μήνες
        mom = c / c.shift(n) - 1
        flip = np.sign(mom) != np.sign(mom.shift())
        return pd.Series(np.where(flip & (mom > 0), 1, np.where(flip & (mom < 0), -1, 0)), index=df.index)
    if name == "bbmr":
        low_adx = df["adx"] < 20
        return pd.Series(np.where(low_adx & (c < df["bb_low"]), 1,
                                  np.where(low_adx & (c > df["bb_up"]), -1, 0)), index=df.index)
    raise ValueError(name)


def exit_now(name: str, df: pd.DataFrame, i: int, side: int, tf: str) -> bool:
    """Κανόνας εξόδου στο κλείσιμο του κεριού i (εκτός από το stop)."""
    c = df["close"]
    if name == "turtle":   # έξοδος στο σπάσιμο του 10-ημερου αντίθετου άκρου
        if side > 0:
            return c.iloc[i] < df["low"].iloc[i - 10:i].min()
        return c.iloc[i] > df["high"].iloc[i - 10:i].max()
    if name == "rsi2":     # έξοδος όταν κλείσει πάνω από τον MA5
        return c.iloc[i] > c.iloc[i - 4:i + 1].mean()
    if name == "tsmom":    # έξοδος όταν γυρίσει το momentum
        n = 90 if tf == "1d" else 540
        return np.sign(c.iloc[i] / c.iloc[i - n] - 1) != side
    if name == "bbmr":     # έξοδος στη μεσαία μπάντα
        return (c.iloc[i] >= df["bb_mid"].iloc[i]) if side > 0 else (c.iloc[i] <= df["bb_mid"].iloc[i])
    raise ValueError(name)


STRATEGIES = {
    "turtle": Strategy("Turtle (Donchian 20/10)", "σπάσιμο υψηλού/χαμηλού 20 κεριών, έξοδος στο 10-κερο άκρο", 2.0),
    "rsi2": Strategy("RSI(2) Connors", "αγορά βουτιάς (RSI2<10) πάνω από MA200, έξοδος πάνω από MA5", 3.0, True),
    "tsmom": Strategy("Time-series momentum", "θέση προς την κατεύθυνση της απόδοσης 3 μηνών", 3.0),
    "bbmr": Strategy("Bollinger mean reversion", "αντίθετα στο άγγιγμα της μπάντας όταν ADX<20, έξοδος στη μέση", 2.0),
}


def run_one(key: str, symbol: str, tf: str, days: int | None = None) -> backtest.Result:
    st = STRATEGIES[key]
    cfg = load_config()
    fee_pct = float(cfg.get("backtest", {}).get("fee_pct", 0.1))
    asset = market.resolve(symbol)
    days = days or backtest.DEFAULT_DAYS[tf]
    warm = int(backtest.WARMUP_BARS * market.TF_DELTA[tf] / pd.Timedelta(days=1)) + 1
    df = indicators.add_all(market.fetch_history(asset, tf, days + warm))
    sig = entries(key, df, tf)
    o, h, l, c, atr = (df[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    bar = market.TF_DELTA[tf]
    trades, i, n = [], backtest.WARMUP_BARS, len(df)
    while i < n - 1:
        side = int(sig.iloc[i])
        if side == 0 or (st.long_only and side < 0):
            i += 1
            continue
        entry, risk = c[i], st.stop_atr * atr[i]
        stop = entry - side * risk
        j, exit_px, status = i + 1, None, "EXIT"
        while j < n:
            if side > 0 and l[j] <= stop:
                exit_px, status = min(o[j], stop), "SL"
            elif side < 0 and h[j] >= stop:
                exit_px, status = max(o[j], stop), "SL"
            elif exit_now(key, df, j, side, tf):
                exit_px = c[j]
            if exit_px is not None:
                break
            j += 1
        if exit_px is None:
            break  # ακόμα ανοιχτό στο τέλος των δεδομένων
        gross = side * (exit_px - entry) / risk
        fee_r = 2 * fee_pct / 100 * entry / risk
        trades.append(backtest.Trade(asset.symbol, df.index[i] + bar, df.index[j] + bar,
                                     "LONG" if side > 0 else "SHORT", 0.0, entry, stop, np.nan, np.nan,
                                     status, gross, fee_r, 0.01, False, df.index[j], exit_px))
        i = j
    return backtest.Result(asset=asset, timeframe=tf, start=df.index[backtest.WARMUP_BARS], end=df.index[-1],
                           bars=n - backtest.WARMUP_BARS, trades=trades, balance=0, risk_pct=1, fee_pct=fee_pct,
                           buy_hold_pct=0)


def summary(label: str, results: list[backtest.Result], kind: str | None) -> dict:
    rs = [r for r in results if kind in (None, r.asset.kind)]
    T = [t for r in rs for t in r.trades]
    old = [t for r in rs for t in r.trades if t.opened < r.split_at]
    new = [t for r in rs for t in r.trades if t.opened >= r.split_at]
    years = sum((r.end - r.start).days / 365 for r in rs)
    s, so, sn = backtest.stats(T), backtest.stats(old), backtest.stats(new)
    pos = sum(1 for r in rs if r.trades and backtest.stats(r.trades)["total_r"] > 0)
    if not s["n"]:
        return {"στρατηγική": label, "τύπος": kind or "όλα", "n": 0}
    return {
        "στρατηγική": label, "τύπος": kind or "όλα", "n": s["n"], "win%": round(s["win_rate"]),
        "avgR": round(s["avg_r"], 3), "R/έτος/σύμβ.": round(s["total_r"] / years, 2) if years else None,
        "PF": round(s["profit_factor"], 2), "παλιά": round(so["avg_r"], 3) if so["n"] else None,
        "νέα": round(sn["avg_r"], 3) if sn["n"] else None, "θετικά": f"{pos}/{len(rs)}",
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 220)
    tf = sys.argv[1] if len(sys.argv) > 1 else "1d"
    symbols = research.CRYPTO + research.STOCKS
    _hist, _orig = {}, market.fetch_history

    def cached(asset, t, d):
        if (asset.symbol, t, d) not in _hist:
            _hist[(asset.symbol, t, d)] = _orig(asset, t, d)
        return _hist[(asset.symbol, t, d)]
    market.fetch_history = cached

    rows = []
    base, _ = backtest.run_many(symbols, tf)
    for kind in ("crypto", "stock"):
        rows.append(summary("Το bot (σήμερα)", base, kind))
    for key, st in STRATEGIES.items():
        res = []
        for sym in symbols:
            try:
                res.append(run_one(key, sym, tf))
            except Exception as e:
                print(f"  {key} {sym}: {e}", file=sys.stderr)
        for kind in ("crypto", "stock"):
            rows.append(summary(st.name, res, kind))
    print(f"\n===== {tf} · 20 σύμβολα =====")
    print(pd.DataFrame(rows).to_string(index=False))
