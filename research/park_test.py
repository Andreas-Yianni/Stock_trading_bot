"""Does parking idle cash in SPY help? Point-in-time S&P 500 test of the current rules (like honest_test.py):

  1. bot only (idle cash stays cash)              – what the Alpaca account does today
  2. bot + idle cash in SPY (sold to fund entries)
  3. 80% SPY buy & hold + 20% bot (separate, no rebalancing)
  4. SPY buy & hold

Usage:  python research/park_test.py   (uses the caches of honest_test.py; run that first)
"""
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import honest_test as ht  # noqa: E402
import lab  # noqa: E402
import port  # noqa: E402
from tradebot.config import load_config  # noqa: E402

PERIODS = {**ht.PERIODS, "όλα 2009-": ("2009-06-01", "2100-01-01")}
FEE = 0.02   # % per SPY buy/sell (spread/slippage; Alpaca has no commission)


def metrics(s: pd.Series) -> dict:
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"cagr": 100 * ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1), "dd": 100 * float((s / s.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(252))}


def worst_year_vs_spy(a: pd.Series, spy: pd.Series):
    ya, ys = a.resample("YE").last().pct_change().dropna(), spy.resample("YE").last().pct_change().dropna()
    d = (ya - ys).dropna() * 100
    return float(d.min()), int((d > 0).sum()), len(d)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    se = ht.members()
    syms = ht.prepare()
    lab.precompute_all(syms)
    syms = [s for s in syms if (lab.PRE / f"{s}.pkl").exists()]
    pres = {s: {"sym": s, "df": lab.load_raw(s)["1d"]} for s in syms}
    v = lab.from_config(load_config()["analysis"])
    with Pool(12) as pool:
        T = ht.member_filter(lab.run(v, syms, pool), se)
    today = str(pd.Timestamp.now().date())
    print(f"\n{len(syms)} μετοχές S&P 500 (μόνο όσο ήταν μέλη) · κανόνες: {v.name} · "
          f"ρίσκο 0.5%/trade, έως 10%/θέση, χωρίς margin\n")
    for k, (a, b) in PERIODS.items():
        b = min(b, today)
        sub = T[(T.opened >= pd.Timestamp(a, tz="UTC")) & (T.opened < pd.Timestamp(b, tz="UTC"))]
        bot = port.simulate(sub, pres, a, b, risk_pct=0.5, max_pos_pct=10)
        park = port.simulate(sub, pres, a, b, risk_pct=0.5, max_pos_pct=10, park="SPY", park_fee_pct=FEE)
        dates, c = port.closes(pres["SPY"])
        spy = pd.Series(c, index=dates).reindex(bot["curve"].index).ffill()
        spy = spy / spy.iloc[0]
        mix = 0.8 * spy + 0.2 * bot["curve"] / bot["curve"].iloc[0]
        rows = [("1. bot μόνο (μετρητά αδρανή)", bot["curve"], f"μέση έκθεση {bot['expo']:.0f}%"),
                ("2. bot + αδρανή μετρητά σε SPY", park["curve"], f"μέση έκθεση σε μετοχές bot {park['expo']:.0f}%"),
                ("3. 80% SPY + 20% bot", mix, ""),
                ("4. SPY αγοράζω-κρατάω", spy, "")]
        print(f"── {k} ({a} → {b}) · trades: {bot['n']}")
        for name, s, extra in rows:
            m = metrics(s)
            w, beat, ny = worst_year_vs_spy(s, spy)
            vs = "" if name.startswith("4.") else f" · νικά το SPY {beat}/{ny} χρόνια, χειρότερη χρονιά {w:+.0f} μον."
            print(f"   {name:34s} {m['cagr']:+5.1f}%/χρ · πτώση {m['dd']:4.0f}% · Sharpe {m['sharpe']:.2f}{vs}"
                  + (f" · {extra}" if extra else ""))
        print()


if __name__ == "__main__":
    main()
