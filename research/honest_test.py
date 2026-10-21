"""Το «τίμιο τεστ»: οι κανόνες του bot σε ΟΛΕΣ τις μετοχές του S&P 500, μόνο όσο ήταν πράγματι μέλη του
δείκτη (2009-σήμερα), ώστε να μη μετράμε μόνο τις σημερινές πετυχημένες (survivorship bias).

Γιατί υπάρχει: με τις 44 μεγάλες μετοχές που ξέρουμε σήμερα, ένα «αγόραζε τις πιο δυνατές μετοχές»
έβγαζε 30-45% τον χρόνο· με την πραγματική σύνθεση του δείκτη έβγαλε λιγότερο από το SPY. Κάθε αλλαγή
κανόνων κρίνεται εδώ, και στα παλιά (2009-2019) ΚΑΙ στα νέα (2020-) δεδομένα.

Πηγές: σύνθεση S&P 500 από github.com/fja05680/sp500 (sp500_ticker_start_end.csv), τιμές από Yahoo.
Λείπουν όσες εταιρείες δεν υπάρχουν πια στο Yahoo (εξαγορές/πτωχεύσεις, ~27% πριν το 2020): μικρή
αισιόδοξη απόκλιση που μένει.

Χρήση:  python research/honest_test.py            (πρώτη φορά ~45 λεπτά: λήψη + precompute)
        python research/honest_test.py --update   (κατεβάζει ξανά τις τιμές, π.χ. κάθε λίγους μήνες)
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
import lab  # noqa: E402
import port  # noqa: E402
from tradebot.config import load_config  # noqa: E402

SP = lab.DATA / "sp500"
MEMBERS_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
PERIODS = {"παλιά 2009-19": ("2009-06-01", "2020-01-01"), "νέα 2020-": ("2020-01-01", "2100-01-01")}


def members() -> pd.DataFrame:
    f = SP / "start_end.csv"
    if not f.exists():
        SP.mkdir(parents=True, exist_ok=True)
        pd.read_csv(MEMBERS_URL).to_csv(f, index=False)
    se = pd.read_csv(f, parse_dates=["start_date", "end_date"])
    se["ticker"] = se.ticker.str.replace(".", "-", regex=False)
    return se


def download(se: pd.DataFrame, update: bool) -> None:
    import yfinance as yf
    f = SP / "prices.pkl"
    if f.exists() and not update:
        return
    tick = sorted(se[se.end_date.isna() | (se.end_date >= "2008-06-01")].ticker.unique())
    out = {}
    for k in range(0, len(tick), 80):
        batch = tick[k:k + 80]
        df = yf.download(batch, start="2008-01-01", auto_adjust=True, group_by="ticker", threads=True,
                         progress=False)
        for t in batch:
            try:
                d = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")
            except KeyError:
                continue
            if len(d) > 50:
                out[t] = d
        print(f"  τιμές: {min(k + 80, len(tick))}/{len(tick)}", flush=True)
        time.sleep(2)
    pickle.dump(out, open(f, "wb"))
    for p in list(lab.HIST.glob("*.pkl")) + list(lab.PRE.glob("*.pkl")):
        p.unlink()   # νέες τιμές: ξαναφτιάχνονται


def prepare() -> list[str]:
    """Τιμές → μορφή του εργαστηρίου (ημερήσια + εβδομαδιαία, όπως τα δίνει το Yahoo)."""
    lab.HIST.mkdir(parents=True, exist_ok=True)
    allp = pickle.load(open(SP / "prices.pkl", "rb"))
    if "SPY" not in allp:   # το SPY δεν είναι μέλος, χρειάζεται για ημερολόγιο και σύγκριση
        import yfinance as yf
        allp["SPY"] = yf.Ticker("SPY").history(start="2008-01-01", auto_adjust=True)
    for t, d in allp.items():
        out = lab.HIST / f"{t}.pkl"
        if out.exists():
            continue
        d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        if len(d) < 400:
            continue
        idx = pd.to_datetime(d.index)
        d.index = (idx.tz_localize("America/New_York") if idx.tz is None else idx).tz_convert("UTC")
        d.index.name = "ts"
        ny = d.index.tz_convert("America/New_York")
        wk = ny.normalize() - pd.to_timedelta(ny.weekday, unit="D")
        w = d.groupby(wk).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        w.index = pd.DatetimeIndex(w.index).tz_convert("UTC")
        pickle.dump({"1d": d, "1w": w}, open(out, "wb"))
    return sorted(p.stem for p in lab.HIST.glob("*.pkl"))


def member_filter(T: pd.DataFrame, se: pd.DataFrame) -> pd.DataFrame:
    iv = {}
    for r in se.itertuples():
        iv.setdefault(r.ticker, []).append((r.start_date, r.end_date if r.end_date == r.end_date
                                            else pd.Timestamp("2100-01-01")))
    d = T.opened.dt.tz_convert("America/New_York").dt.tz_localize(None)
    ok = [any(a <= x <= b for a, b in iv.get(s, [])) for s, x in zip(T.sym, d)]
    return T[np.array(ok, dtype=bool)].reset_index(drop=True)


def member_years(se, syms, a, b):
    a, b = pd.Timestamp(a), min(pd.Timestamp(b), pd.Timestamp.now())
    have = set(syms)
    tot = 0.0
    for r in se.itertuples():
        if r.ticker in have:
            lo, hi = max(r.start_date, a), min(r.end_date if r.end_date == r.end_date else b, b)
            tot += max((hi - lo).days, 0) / 365.25
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="κατέβασε ξανά τιμές και σύνθεση")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.update:
        (SP / "start_end.csv").unlink(missing_ok=True)
    se = members()
    download(se, args.update)
    syms = prepare()
    lab.precompute_all(syms)
    syms = [s for s in syms if (lab.PRE / f"{s}.pkl").exists()]

    analysis = load_config()["analysis"]
    cur = lab.from_config(analysis)
    variants = [cur, lab.V("παλιοί σταθεροί στόχοι (TP1/TP2, 30 μέρες)", threshold=cur.threshold)]
    if cur.trail:
        variants += [replace(cur, name=f"trailing {k} ATR", trail=k) for k in (5, 8) if k != cur.trail]
    variants += [replace(cur, name="όριο σκορ 20", threshold=20), replace(cur, name="όριο σκορ 40", threshold=40),
                 replace(cur, name="χωρίς σήμα (πάντα LONG όταν δεν έχεις θέση)", threshold=-1000, mode="any")]

    my = {k: member_years(se, syms, a, b) for k, (a, b) in PERIODS.items()}
    pres = {s: {"sym": s, "df": lab.load_raw(s)["1d"]} for s in syms}
    print(f"\n{len(syms)} μετοχές του S&P 500 (μόνο όσο ήταν μέλη) · R/χρόνο ανά μετοχή-μέλος\n")
    with Pool(12) as pool:
        for v in variants:
            T = member_filter(lab.run(v, syms, pool), se)
            cells = []
            for k, (a, b) in PERIODS.items():
                sub = T[(T.opened >= pd.Timestamp(a, tz="UTC")) & (T.opened < pd.Timestamp(b, tz="UTC"))]
                s = lab.stats(sub.r)
                p = port.simulate(sub, pres, a, min(b, str(pd.Timestamp.now().date())), risk_pct=0.5, max_pos_pct=10)
                cells.append(f"{k}: {s['tot'] / my[k]:+.2f}R/χρ · Μ.Ο. {s['avg']:+.2f}R · n={s['n']} · "
                             f"λογαριασμός {p['cagr']:+.1f}%/χρ (πτώση {p['dd']:.0f}%)")
            print(f"{v.name}\n   " + "\n   ".join(cells), flush=True)
    print()
    for k, (a, b) in PERIODS.items():
        r = port.bh(pres, "SPY", a, min(b, str(pd.Timestamp.now().date())))
        print(f"SPY αγοράζω-κρατάω {k}: {r['cagr']:+.1f}%/χρ (πτώση {r['dd']:.0f}%)")
    print("\nΛογαριασμός: 100.000, ρίσκο 0.5% ανά trade, έως 10% ανά θέση, χωρίς margin, όλο το S&P 500.")


if __name__ == "__main__":
    main()
