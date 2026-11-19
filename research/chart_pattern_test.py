"""Point-in-time S&P 500 test of classic bullish chart patterns (research/chart_patterns.py) as entries,
with (a) each pattern's own stop + measured-move target and (b) the bot's trend exit (6 ATR).
Each also combined with the bot's score >= threshold ("trend confirmation").

Usage:  python research/chart_pattern_test.py [--check]   (--check = lookahead test only)
"""
import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_patterns as cp  # noqa: E402
import honest_test as ht  # noqa: E402
import lab  # noqa: E402
import port  # noqa: E402
from tradebot.config import load_config  # noqa: E402

AN = load_config()["analysis"]
THR = AN["min_score_for_signal"]
TRAIL = float(AN.get("trend_atr", 6))
KEYS = [(p, ex, ws) for p in cp.PATTERNS for ex in ("target", "trend") for ws in (False, True)]


def _work(sym):
    pre = lab._get(sym)
    df = pre["df"]
    ev = cp.detect(df)
    out = {}
    for p, ex, ws in KEYS:
        out[(p, ex, ws)] = cp.simulate(sym, df, ev[p], ex, score=pre["score"], thr=THR if ws else -1e9,
                                       trail=TRAIL)
    return out


def check(syms):
    """No lookahead: signals found on truncated data must equal those on full data up to the cut."""
    bad = 0
    for s in syms:
        df = lab._get(s)["df"]
        full = cp.detect(df)
        for cut in (1500, 2500, 3500):
            if cut >= len(df):
                continue
            part = cp.detect(df.iloc[:cut])
            for p in cp.PATTERNS:
                a = [e for e in full[p] if e[0] < cut]
                b = part[p]
                if [(i, round(x, 6), round(y, 6)) for i, x, y in a] != [(i, round(x, 6), round(y, 6)) for i, x, y in b]:
                    bad += 1
                    print(f"  LOOKAHEAD? {s} {p} cut={cut}: {len(a)} vs {len(b)}")
    print("lookahead check:", "OK" if not bad else f"{bad} mismatches")
    return bad == 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    se = ht.members()
    syms = ht.prepare()
    lab.precompute_all(syms)
    syms = [s for s in syms if (lab.PRE / f"{s}.pkl").exists()]
    if not check(["AAPL", "JPM", "XOM", "KO", "NVDA"]) or "--check" in sys.argv:
        return
    pres = {s: {"sym": s, "df": lab.load_raw(s)["1d"]} for s in syms}
    with Pool(12) as pool:
        parts = pool.map(_work, syms, chunksize=8)
    my = {k: ht.member_years(se, syms, a, b) for k, (a, b) in ht.PERIODS.items()}
    today = str(pd.Timestamp.now().date())
    print(f"\n{len(syms)} μετοχές S&P 500 (μόνο όσο ήταν μέλη) · ρίσκο 0.5%/trade, έως 10%/θέση, χωρίς margin")
    print("έξοδος «στόχος» = stop κάτω από το σχήμα + στόχος το ύψος του · «τάση» = stop του bot (6 ATR)\n")
    for p in cp.PATTERNS:
        print(f"── {cp.NAMES[p]}")
        for ex in ("target", "trend"):
            for ws in (False, True):
                T = pd.DataFrame([t for d in parts for t in d[(p, ex, ws)]])
                name = f"{'στόχος' if ex == 'target' else 'τάση  '}{' + σκορ' if ws else '       '}"
                if not len(T):
                    print(f"   {name}  κανένα trade")
                    continue
                T = ht.member_filter(T, se)
                cells = []
                for k, (a, b) in ht.PERIODS.items():
                    b = min(b, today)
                    sub = T[(T.opened >= pd.Timestamp(a, tz="UTC")) & (T.opened < pd.Timestamp(b, tz="UTC"))]
                    s = lab.stats(sub.r)
                    acc = ""
                    if s["n"] >= 20:
                        r = port.simulate(sub, pres, a, b, risk_pct=0.5, max_pos_pct=10)
                        acc = f" · λογ. {r['cagr']:+5.1f}%/χρ πτώση {r['dd']:4.0f}% έκθεση {r['expo']:3.0f}%"
                    cells.append(f"{k}: n={s['n']:5d} επιτ.{s['win']:3.0f}% Μ.Ο.{s['avg']:+.2f}R "
                                 f"{s['tot'] / my[k]:+.2f}R/χρ{acc}")
                print(f"   {name}  {cells[0]}\n   {'':15s}  {cells[1]}", flush=True)
        print()
    for k, (a, b) in ht.PERIODS.items():
        r = port.bh(pres, "SPY", a, min(b, today))
        print(f"SPY αγοράζω-κρατάω {k}: {r['cagr']:+.1f}%/χρ (πτώση {r['dd']:.0f}%)")
    print("Τωρινοί κανόνες (honest_test): 2009-19 +13.1%/χρ, 2020- +10.8%/χρ")


if __name__ == "__main__":
    main()
