"""Earnings surprise / «post-earnings drift» (PEAD) στο τίμιο τεστ του S&P 500.

Ερώτηση: αν αγοράζαμε μετά από ΚΑΛΑ αποτελέσματα (EPS πάνω από την πρόβλεψη και/ή θετική αντίδραση της τιμής),
θα βγάζαμε περισσότερα από τους τωρινούς κανόνες και από το SPY;

Χωρίς κλεφτή ματιά στο μέλλον:
- Ημερομηνία/ώρα και EPS από το Yahoo (yfinance get_earnings_dates). Ανακοίνωση πριν τις 09:30 Νέας Υόρκης
  → «μέρα αντίδρασης» = η ίδια μέρα· αλλιώς (μετά το κλείσιμο ή άγνωστη ώρα) → η επόμενη συνεδρίαση.
- Το σήμα είναι στο κλείσιμο της μέρας αντίδρασης και η είσοδος στο ΑΝΟΙΓΜΑ της επόμενης (όπως η πρωινή
  σάρωση του bot, που βλέπει μόνο κλειστά κεριά). Ίδια μηχανή με το honest_test (research/lab.py): ίδιο
  stop, έξοδος, προμήθειες, ένας λογαριασμός χωρίς margin, μόνο όσο η μετοχή ήταν μέλος του δείκτη.
Όριο: για εταιρείες που δεν υπάρχουν πια στο Yahoo λείπουν και τιμές και αποτελέσματα (όπως στο honest_test).

Χρήση:  python research/pead.py            (πρώτη φορά ~15 λεπτά για τα αποτελέσματα, μετά λίγα λεπτά)
        python research/pead.py --update   (κατεβάζει ξανά τα αποτελέσματα)
"""
import argparse
import pickle
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import honest_test as ht  # noqa: E402
import lab  # noqa: E402
import port  # noqa: E402
from tradebot.config import load_config  # noqa: E402

EARN = ht.SP / "earnings.pkl"
_EARN = None


def download(syms: list[str], update: bool) -> dict:
    """{ticker: DataFrame(index = ώρα ανακοίνωσης NY, EPS Estimate, Reported EPS, Surprise(%))}. Συνεχίζει
    από όπου σταμάτησε (αποθήκευση ανά 25 μετοχές)."""
    import yfinance as yf
    out = {} if update or not EARN.exists() else pickle.load(open(EARN, "rb"))
    todo = [s for s in syms if s not in out and s != "SPY"]
    for k, s in enumerate(todo):
        try:
            e = yf.Ticker(s).get_earnings_dates(limit=100)
            out[s] = None if e is None or e.empty else e.dropna(subset=["Reported EPS"])
        except Exception:
            out[s] = None
        if (k + 1) % 25 == 0 or k + 1 == len(todo):
            pickle.dump(out, open(EARN, "wb"))
            print(f"  αποτελέσματα: {k + 1}/{len(todo)}", flush=True)
        time.sleep(0.3)
    return out


def _earnings(sym):
    global _EARN
    if _EARN is None:
        _EARN = pickle.load(open(EARN, "rb"))
    return _EARN.get(sym)


def reaction_index(df: pd.DataFrame, e: pd.DataFrame) -> pd.DataFrame:
    """Για κάθε ανακοίνωση: θέση της μέρας αντίδρασης στο df, έκπληξη EPS, απόδοση της μέρας αντίδρασης."""
    if e is None or not len(e):
        return pd.DataFrame(columns=["i", "surprise", "react"])
    days = df.index.tz_convert("America/New_York").normalize().tz_localize(None)
    c = df["close"].to_numpy()
    rows = []
    for ts, r in e.iterrows():
        t = ts.tz_convert("America/New_York") if ts.tzinfo else ts.tz_localize("America/New_York")
        d = t.normalize().tz_localize(None)
        before_open = t.hour * 60 + t.minute < 9 * 60 + 30 and not (t.hour == 0 and t.minute == 0)
        i = int(days.searchsorted(d, side="left" if before_open else "right"))
        if i <= 0 or i >= len(df):
            continue
        rows.append({"i": i, "surprise": r.get("Surprise(%)"), "react": c[i] / c[i - 1] - 1})
    return pd.DataFrame(rows)


def make_signal(min_surprise=None, min_react=None):
    def sig(pre):
        out = np.zeros(len(pre["df"]), dtype=bool)
        ev = reaction_index(pre["df"], _earnings(pre["sym"]))
        for r in ev.itertuples():
            ok = True
            if min_surprise is not None:
                ok &= r.surprise == r.surprise and r.surprise >= min_surprise
            if min_react is not None:
                ok &= r.react >= min_react
            if ok:
                out[r.i] = True
        return out
    return sig


SIGNALS = {
    "pead_s0": make_signal(min_surprise=0.0),
    "pead_s5": make_signal(min_surprise=5.0),
    "pead_s5_r2": make_signal(min_surprise=5.0, min_react=0.02),
    "pead_r4": make_signal(min_react=0.04),
    "pead_any": make_signal(),
}


def register():
    """Και στους workers του Pool (Windows: spawn), ώστε το lab να βρίσκει τα νέα σήματα."""
    lab.SIGNALS.update(SIGNALS)


register()


def event_study(syms, se):
    """Μέση κίνηση της μετοχής μείον SPY, από το άνοιγμα μετά τη μέρα αντίδρασης, σε 5/20/60 μέρες."""
    spy = lab.load_raw("SPY")["1d"]
    spy_c = pd.Series(spy["close"].to_numpy(), index=spy.index.tz_convert("America/New_York").normalize())
    rows = []
    for s in syms:
        if s == "SPY":
            continue
        df = lab.load_raw(s)["1d"]
        ev = reaction_index(df, _earnings(s))
        if not len(ev):
            continue
        o, c = df["open"].to_numpy(), df["close"].to_numpy()
        days = df.index.tz_convert("America/New_York").normalize()
        sc = spy_c.reindex(days).ffill().to_numpy()
        for r in ev.itertuples():
            i0 = r.i + 1
            if i0 + 60 >= len(df) or sc[r.i] != sc[r.i]:
                continue
            rec = {"sym": s, "date": days[r.i].tz_localize(None), "surprise": r.surprise, "react": r.react}
            for h in (5, 20, 60):
                rec[f"x{h}"] = (c[i0 + h - 1] / o[i0] - 1) - (sc[i0 + h - 1] / sc[r.i] - 1)
            rows.append(rec)
    E = pd.DataFrame(rows)
    ok = []
    iv = {}
    for r in se.itertuples():
        iv.setdefault(r.ticker, []).append((r.start_date, r.end_date if r.end_date == r.end_date
                                            else pd.Timestamp("2100-01-01")))
    for s, d in zip(E.sym, E.date):
        ok.append(any(a <= d <= b for a, b in iv.get(s, [])))
    E = E[np.array(ok, dtype=bool)]
    E = E[E.date >= "2009-06-01"]
    groups = {
        "όλες οι ανακοινώσεις": E,
        "έκπληξη EPS ≥ 0": E[E.surprise >= 0],
        "έκπληξη EPS < 0": E[E.surprise < 0],
        "έκπληξη ≥ 5% και αντίδραση ≥ +2%": E[(E.surprise >= 5) & (E.react >= 0.02)],
        "αντίδραση ≥ +4%": E[E.react >= 0.04],
        "αντίδραση ≤ -4%": E[E.react <= -0.04],
    }
    print("\nΚίνηση ΜΕΤΑ την αντίδραση (είσοδος στο επόμενο άνοιγμα), μείον SPY · μέσος όρος (t-score):")
    for name, g in groups.items():
        cells = []
        for h in (5, 20, 60):
            x = g[f"x{h}"].dropna()
            t = x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 1 else 0
            cells.append(f"{h}μ {x.mean() * 100:+.2f}% (t {t:+.1f})")
        old = g[g.date < "2020-01-01"]["x20"].mean() * 100
        new = g[g.date >= "2020-01-01"]["x20"].mean() * 100
        print(f"  {name:36s} n={len(g):5d} · " + " · ".join(cells) + f" · 20μ παλιά {old:+.2f}% / νέα {new:+.2f}%")
    print("  (t ≈ ±2 = πιθανώς όχι τύχη· οι ανακοινώσεις επικαλύπτονται χρονικά, οπότε το t είναι αισιόδοξο)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--procs", type=int, default=4)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    se = ht.members()
    ht.download(se, False)
    syms = ht.prepare()
    lab.precompute_all(syms, procs=args.procs)
    syms = [s for s in syms if (lab.PRE / f"{s}.pkl").exists()]
    earn = download(syms, args.update)
    have = sum(1 for s in syms if earn.get(s) is not None and len(earn[s]))
    print(f"\nΑποτελέσματα βρέθηκαν για {have}/{len(syms) - 1} μετοχές")

    event_study(syms, se)

    cur = lab.from_config(load_config()["analysis"])
    variants = [cur] + [replace(cur, name=n, signal=k) for k, n in [
        ("pead_any", "μετά από ΚΑΘΕ ανακοίνωση (σύγκριση)"),
        ("pead_s0", "έκπληξη EPS ≥ 0"),
        ("pead_s5", "έκπληξη EPS ≥ 5%"),
        ("pead_s5_r2", "έκπληξη ≥ 5% και αντίδραση τιμής ≥ +2%"),
        ("pead_r4", "αντίδραση τιμής ≥ +4% (μόνο τιμή)"),
    ]]
    variants += [replace(v, name=v.name + " · έξοδος σε 60 μέρες", max_days=60, trail=None, trail_from_start=False,
                         tp1=1000.0, tp2=None, part=0.0, be=False) for v in variants[3:5]]

    my = {k: ht.member_years(se, syms, a, b) for k, (a, b) in ht.PERIODS.items()}
    pres = {s: {"sym": s, "df": lab.load_raw(s)["1d"]} for s in syms}
    print(f"\n{len(syms)} μετοχές του S&P 500 (μόνο όσο ήταν μέλη) · R/χρόνο ανά μετοχή-μέλος\n")
    with Pool(args.procs, initializer=register) as pool:
        for v in variants:
            T = ht.member_filter(lab.run(v, syms, pool), se)
            cells = []
            for k, (a, b) in ht.PERIODS.items():
                sub = T[(T.opened >= pd.Timestamp(a, tz="UTC")) & (T.opened < pd.Timestamp(b, tz="UTC"))]
                s = lab.stats(sub.r)
                p = port.simulate(sub, pres, a, min(b, str(pd.Timestamp.now().date())), risk_pct=0.5, max_pos_pct=10)
                cells.append(f"{k}: {s['tot'] / my[k]:+.2f}R/χρ · Μ.Ο. {s['avg']:+.2f}R · n={s['n']} · "
                             f"λογαριασμός {p['cagr']:+.1f}%/χρ (πτώση {p['dd']:.0f}%)")
            print(f"{v.name}\n   " + "\n   ".join(cells), flush=True)
    print()
    for k, (a, b) in ht.PERIODS.items():
        r = port.bh(pres, "SPY", a, min(b, str(pd.Timestamp.now().date())))
        print(f"SPY αγοράζω-κρατάω {k}: {r['cagr']:+.1f}%/χρ (πτώση {r['dd']:.0f}%)")
    print("\nΛογαριασμός: 100.000, ρίσκο 0.5% ανά trade, έως 10% ανά θέση, χωρίς margin, όλο το S&P 500.")


if __name__ == "__main__":
    main()
