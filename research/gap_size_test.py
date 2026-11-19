"""Gap-up opens: does sizing from the actual open (instead of the plan's entry) or skipping big gaps help?
Point-in-time S&P 500 test of the current rules (like honest_test.py / park_test.py):

  1. size from plan entry - SL (what the bot does today)
  2. size from actual fill (next open) - SL   -> loss at the initial stop is always ~1R of account risk
  3. like 1, but skip trades that open > 0.5 plan-R above the plan entry
  4. like 1, but skip trades that open > 0.25 plan-R above the plan entry

Usage:  python research/gap_size_test.py   (uses the caches of honest_test.py)
"""
import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import honest_test as ht  # noqa: E402
import lab  # noqa: E402
import park_test as pk  # noqa: E402
import port  # noqa: E402
from tradebot.config import load_config  # noqa: E402

VARIANTS = [("1. μέγεθος από plan (σήμερα)", {}),
            ("2. μέγεθος από άνοιγμα", {"size_on": "fill"}),
            ("3. plan, χωρίς κενό > 0.5R", {"max_gap_r": 0.5}),
            ("4. plan, χωρίς κενό > 0.25R", {"max_gap_r": 0.25})]


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
    gap = (T.entry - T.plan_entry) / T.risk
    print(f"\n{len(syms)} μετοχές, {len(T)} trades · κενό ανοίγματος σε R του plan: διάμεσος {gap.median():+.2f}, "
          f">0.25R {100 * (gap > 0.25).mean():.0f}%, >0.5R {100 * (gap > 0.5).mean():.0f}%, >1R {100 * (gap > 1).mean():.1f}%")
    for lo, hi in ((-9, 0), (0, 0.25), (0.25, 0.5), (0.5, 99)):
        m = (gap > lo) & (gap <= hi)
        print(f"   κενό {lo:+.2f}..{hi:+.2f}R: {m.sum():6d} trades, μ.ο. {T.r[m].mean():+.3f}R (plan), "
              f"κερδισμένα {100 * (T.r[m] > 0).mean():.0f}%")
    today = str(pd.Timestamp.now().date())
    for k, (a, b) in pk.PERIODS.items():
        b = min(b, today)
        sub = T[(T.opened >= pd.Timestamp(a, tz="UTC")) & (T.opened < pd.Timestamp(b, tz="UTC"))]
        dates, c = port.closes(pres["SPY"])
        print(f"\n── {k} ({a} → {b})")
        spy = None
        for name, kw in VARIANTS:
            r = port.simulate(sub, pres, a, b, risk_pct=0.5, max_pos_pct=10, **kw)
            if spy is None:
                spy = pd.Series(c, index=dates).reindex(r["curve"].index).ffill()
            w, beat, ny = pk.worst_year_vs_spy(r["curve"], spy)
            print(f"   {name:30s} {r['cagr']:+5.1f}%/χρ · πτώση {r['dd']:4.0f}% · Sharpe {r['sharpe']:.2f} · "
                  f"trades {r['n']} · νικά SPY {beat}/{ny} χρόνια")
        m = pk.metrics(spy / spy.iloc[0])
        print(f"   {'SPY αγοράζω-κρατάω':30s} {m['cagr']:+5.1f}%/χρ · πτώση {m['dd']:4.0f}% · Sharpe {m['sharpe']:.2f}")


if __name__ == "__main__":
    main()
