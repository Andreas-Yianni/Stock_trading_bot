"""Chart patterns (tradebot/patterns.py) as ENTRY signals, with the bot's current exit (trend stop),
point-in-time on the S&P 500 (like honest_test.py). For each pattern: alone, and together with
score >= threshold. Compared with the current rules, "always LONG" and SPY buy & hold.

Usage:  python research/pattern_test.py   (uses the caches of honest_test.py)
"""
import sys
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import honest_test as ht  # noqa: E402
import lab  # noqa: E402
import port  # noqa: E402
from tradebot import patterns  # noqa: E402
from tradebot.config import load_config  # noqa: E402

PATS = ["pat_structure", "pat_breakout", "pat_engulfing", "pat_pin", "pat_pin_level", "pat_divergence",
        "pat_squeeze", "pat_double", "pat_pullback"]
THR = load_config()["analysis"]["min_score_for_signal"]
_PAT = {}


def _pat(pre):
    s = pre["sym"]
    if s not in _PAT:
        _PAT[s] = patterns.add_all(pre["df"])
    return _PAT[s]


def _make(col, with_score):
    def f(pre):
        sig = (_pat(pre)[col] == 1).to_numpy()
        return sig & (pre["score"] >= THR) if with_score else sig
    return f


# registered at import time, so the spawned worker processes (Windows) have them too
for _c in PATS:
    lab.SIGNALS[_c] = _make(_c, False)
    lab.SIGNALS[_c + "+score"] = _make(_c, True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    se = ht.members()
    syms = ht.prepare()
    lab.precompute_all(syms)
    syms = [s for s in syms if (lab.PRE / f"{s}.pkl").exists()]
    pres = {s: {"sym": s, "df": lab.load_raw(s)["1d"]} for s in syms}
    cur = lab.from_config(load_config()["analysis"])
    variants = [cur, replace(cur, name="πάντα LONG (χωρίς σήμα)", signal="always", mode="any")]
    for c in PATS:
        label = patterns.NAMES[c][0]
        variants += [replace(cur, name=f"{label}", signal=c),
                     replace(cur, name=f"{label} + σκορ≥{THR:g}", signal=c + "+score")]
    my = {k: ht.member_years(se, syms, a, b) for k, (a, b) in ht.PERIODS.items()}
    today = str(pd.Timestamp.now().date())
    print(f"\n{len(syms)} μετοχές S&P 500 (μόνο όσο ήταν μέλη) · έξοδος: stop τάσης {cur.trail:g} ATR · "
          f"ρίσκο 0.5%/trade, έως 10%/θέση\n")
    hdr = "   ".join(f"{k:^52s}" for k in ht.PERIODS)
    print(f"{'':34s} {hdr}")
    with Pool(12) as pool:
        for v in variants:
            T = lab.run(v, syms, pool)
            if not len(T):
                print(f"{v.name[:34]:34s} κανένα trade", flush=True)
                continue
            T = ht.member_filter(T, se)
            cells = []
            for k, (a, b) in ht.PERIODS.items():
                b = min(b, today)
                sub = T[(T.opened >= pd.Timestamp(a, tz="UTC")) & (T.opened < pd.Timestamp(b, tz="UTC"))] \
                    if len(T) else T
                s = lab.stats(sub.r if len(sub) else [])
                if s["n"] >= 20:
                    p = port.simulate(sub, pres, a, b, risk_pct=0.5, max_pos_pct=10)
                    acc = f"{p['cagr']:+5.1f}%/χρ πτώση {p['dd']:4.0f}%"
                else:
                    acc = "  (λίγα trades)       "
                cells.append(f"n={s['n']:5d} Μ.Ο.{s['avg']:+.2f}R {s['tot'] / my[k]:+.2f}R/χρ {acc}")
            print(f"{v.name[:34]:34s} " + "   ".join(cells), flush=True)
    print()
    for k, (a, b) in ht.PERIODS.items():
        r = port.bh(pres, "SPY", a, min(b, today))
        print(f"SPY αγοράζω-κρατάω {k}: {r['cagr']:+.1f}%/χρ (πτώση {r['dd']:.0f}%)")


if __name__ == "__main__":
    main()
