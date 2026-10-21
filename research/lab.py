"""Γρήγορο «εργαστήριο» έρευνας για τους κανόνες του bot (μετοχές, 1d).

Υπολογίζει ΜΙΑ φορά ανά μετοχή, για κάθε κερί, το σκορ και τα επίπεδα S/R ακριβώς όπως το engine
(engine._trend_factors, levels.find_levels), και μετά δοκιμάζει πολλές παραλλαγές εισόδου/εξόδου σε
δευτερόλεπτα. Επαληθεύτηκε (17/10/2026) ότι βγάζει ΑΚΡΙΒΩΣ τα ίδια trades με το tradebot.backtest.run
(και με σταθερούς στόχους και με έξοδο τάσης). Χρησιμοποιείται από το research/honest_test.py.

Τα αποτελέσματα του precompute μένουν στο data/research/pre/ και ξαναϋπολογίζονται μόνα τους όταν
αλλάξει ο κώδικας των δεικτών / του σκορ / των επιπέδων (VERSION).
"""
import hashlib
import inspect
import pickle
import sys
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tradebot import engine, indicators, levels  # noqa: E402

DATA = ROOT / "data" / "research"
HIST = DATA / "hist"     # {"1d": df, "1w": df} ανά μετοχή (index σε UTC)
PRE = DATA / "pre"
WARMUP = 250


def code_version() -> str:
    src = inspect.getsource(indicators) + inspect.getsource(engine._trend_factors) + inspect.getsource(levels)
    return hashlib.sha1(src.encode()).hexdigest()[:12]


def check_version() -> None:
    """Αν άλλαξαν οι δείκτες/το σκορ/τα επίπεδα, τα παλιά precompute δεν ισχύουν: σβήνονται."""
    PRE.mkdir(parents=True, exist_ok=True)
    vf = PRE / "VERSION"
    if not vf.exists() or vf.read_text().strip() != code_version():
        for p in PRE.glob("*.pkl"):
            p.unlink()
        vf.write_text(code_version())


def load_raw(sym):
    return pickle.load(open(HIST / f"{sym}.pkl", "rb"))


def precompute(sym, start=None):
    """Ανά κερί: σκορ, σκορ κάθε factor, κοντινότερες στηρίξεις/αντιστάσεις."""
    out = PRE / f"{sym}.pkl"
    if out.exists() and start is None:
        return pickle.load(open(out, "rb"))
    raw = load_raw(sym)
    d = raw["1d"] if start is None else raw["1d"][raw["1d"].index >= start]
    df = indicators.add_all(d)
    htf = indicators.add_all(raw["1w"])
    htf_close = htf.index + pd.Timedelta(weeks=1)
    close_times = df.index + pd.Timedelta(days=1)
    n = len(df)
    score = np.full(n, np.nan)
    fac, sup, res = {}, [None] * n, [None] * n
    for i in range(WARMUP, n):
        window = df.iloc[: i + 1]
        h = htf.iloc[: int(htf_close.searchsorted(close_times[i], side="right"))]
        fs = engine._trend_factors(window, h, "1w")
        score[i] = 100 * sum(f.score * f.weight for f in fs) / sum(f.weight for f in fs)
        for f in fs:
            fac.setdefault(f.name, np.full(n, np.nan))[i] = f.score
        lv = levels.find_levels(window)
        sup[i], res[i] = lv["support"], lv["resistance"]
    pre = {"sym": sym, "df": df, "score": score, "fac": fac, "sup": sup, "res": res}
    if start is None:
        pickle.dump(pre, open(out, "wb"))
    return pre


def _pre_worker(sym):
    try:
        precompute(sym)
        return sym, None
    except Exception as e:
        return sym, repr(e)


def precompute_all(syms, procs=12):
    check_version()
    todo = [s for s in syms if not (PRE / f"{s}.pkl").exists()]
    if not todo:
        return
    print(f"precompute {len(todo)} μετοχών (μία φορά, ~{len(todo) // 20 + 1} λεπτά)...", flush=True)
    with Pool(procs) as p:
        for k, (sym, err) in enumerate(p.imap_unordered(_pre_worker, todo)):
            if err:
                print(f"  {sym}: {err}", flush=True)
            if (k + 1) % 50 == 0:
                print(f"  {k + 1}/{len(todo)}", flush=True)


@dataclass(frozen=True)
class V:
    """Μία παραλλαγή κανόνων. Προεπιλογές = οι ΠΑΛΙΟΙ σταθεροί στόχοι· βλ. from_config για τους τωρινούς."""
    name: str = "base"
    threshold: float = 30
    entry: str = "open"          # open: άνοιγμα επόμενου κεριού (όπως ο χρήστης) | close: κλείσιμο σήματος
    mode: str = "fresh"          # fresh: μόνο όταν η πλευρά γίνεται LONG (όπως το live) | any: κάθε LONG κερί
    sl: str = "sr"               # sr (κανόνας engine) | atr
    sl_atr: float = 1.5
    sr_min: float = 1.0
    sr_max: float = 3.0
    tp1: float = 1.5
    tp2: float | None = 3.0      # None = χωρίς TP2
    part: float = 0.5            # τι μέρος κλείνει στο TP1 (0 = τίποτα)
    be: bool = True              # stop στην είσοδο μετά το TP1
    trail: float | None = None   # trailing stop (ATR) μετά το TP1, ή από την αρχή με trail_from_start
    trail_from_start: bool = False
    trail_fixed: bool = False    # απόσταση = k × ATR της εισόδου (αντί για το τρέχον ATR)
    max_days: int | None = 30
    fee_pct: float = 0.1
    signal: str | None = None    # άλλο σήμα εισόδου από τα SIGNALS (αντί για σκορ ≥ threshold)
    gap: bool = True             # gap πέρα από stop/στόχο: εκτέλεση στο άνοιγμα (ρεαλιστικό)


def from_config(analysis: dict, name="τωρινοί κανόνες") -> V:
    """Οι κανόνες του config.yaml (analysis) ως παραλλαγή του εργαστηρίου."""
    thr = analysis["min_score_for_signal"]
    if analysis.get("exit_mode") == "trend":
        return V(name, threshold=thr, tp2=None, max_days=None, part=0.0, be=False,
                 trail=float(analysis.get("trend_atr", 6)), trail_from_start=True)
    return V(name, threshold=thr, tp1=analysis.get("min_reward_risk", 1.5))


def sig_always(pre):
    return np.ones(len(pre["df"]), dtype=bool)


def sig_hi(n):
    def f(pre):
        c = pre["df"]["close"]
        return (c >= c.rolling(n).max()).to_numpy()
    return f


def sig_rsi2(pre):
    c = pre["df"]["close"]
    return ((c > c.rolling(200).mean()) & (indicators.rsi(c, 2) < 10)).to_numpy()


def sig_pullback(pre):
    df = pre["df"]
    return ((df.close > df.ema200) & (df.ema20 > df.ema50) & (df.rsi < 45)).to_numpy()


SIGNALS = {"always": sig_always, "hi20": sig_hi(20), "hi55": sig_hi(55), "hi252": sig_hi(252),
           "rsi2": sig_rsi2, "pullback": sig_pullback}


def build_plan(v: V, pre, i):
    df = pre["df"]
    entry = float(df["close"].iat[i])
    a = float(df["atr"].iat[i])
    sl = entry - v.sl_atr * a
    if v.sl == "sr":
        for level, _t in pre["sup"][i]:
            cand = level - 0.25 * a
            if v.sr_min * a <= entry - cand <= v.sr_max * a:
                sl = cand
                break
    risk = entry - sl
    return entry, sl, entry + v.tp1 * risk, (entry + v.tp2 * risk if v.tp2 else None), a


def simulate_symbol(v: V, pre, start=None, end=None):
    """Trades (dicts) μιας μετοχής, μόνο LONG, σε ημερήσια κεριά. Αν στο ίδιο κερί χτυπούν stop και
    στόχος, υποθέτουμε το χειρότερο. R = σε μονάδες του ρίσκου του plan (η ποσότητα βγαίνει από αυτό)."""
    df = pre["df"]
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = df["atr"].to_numpy()
    idx = df.index
    score = pre["score"]
    n = len(df)
    long_sig = SIGNALS[v.signal](pre) if v.signal else score >= v.threshold
    trades = []
    i = WARMUP if start is None else max(WARMUP, int(idx.searchsorted(start)))
    i1 = n if end is None else int(idx.searchsorted(end))
    prev_long = bool(long_sig[i - 1]) if i > 0 else False
    while i < i1 - 1:
        sig = bool(long_sig[i])
        fresh = sig and not prev_long
        prev_long = sig
        if not (sig if v.mode == "any" else fresh):
            i += 1
            continue
        entry_plan, sl, tp1, tp2, a = build_plan(v, pre, i)
        risk_plan = entry_plan - sl
        if v.entry == "open":
            fill = o[i + 1]
            if fill <= sl:
                i += 1
                continue
        else:
            fill = entry_plan
        expiry = idx[i] + pd.Timedelta(days=v.max_days + 1) if v.max_days else None
        stop, hit1, j1, p1, extreme = sl, False, None, None, None
        realized, rem, status, exit_px = 0.0, 1.0, None, None
        j = i + 1
        while j < n:
            if expiry is not None and idx[j] >= expiry:
                j -= 1
                exit_px = c[j]
                realized += rem * (exit_px - fill) / risk_plan
                status = "TIME"
                break
            if l[j] <= stop:
                px = o[j] if v.gap and o[j] < stop else stop
                realized += rem * (px - fill) / risk_plan
                status, exit_px = ("SL" if stop <= sl else "STOP") if not hit1 else "BE", px
                break
            if not hit1:
                if tp2 is not None and v.part < 1 and h[j] >= tp2 and v.trail is None:
                    px = o[j] if v.gap and o[j] > tp2 else tp2
                    p1x = o[j] if v.gap and o[j] > tp1 else tp1
                    realized += v.part * (p1x - fill) / risk_plan + (1 - v.part) * (px - fill) / risk_plan
                    rem, status, exit_px = 0, "TP2", px
                    break
                if h[j] >= tp1 and not (v.trail_from_start and v.part == 0 and not v.be):
                    px = o[j] if v.gap and o[j] > tp1 else tp1
                    realized += v.part * (px - fill) / risk_plan
                    rem, hit1, j1, p1 = 1 - v.part, True, j, px
                    if rem <= 1e-9:
                        status, exit_px = "TP1", px
                        break
                    if v.be:
                        stop = max(stop, fill)
                    extreme = h[j] if extreme is None else max(extreme, h[j])
                    if v.trail is not None:
                        stop = max(stop, extreme - v.trail * (a if v.trail_fixed else atr[j]))
                    j += 1
                    continue
                if v.trail_from_start and v.trail is not None:
                    extreme = h[j] if extreme is None else max(extreme, h[j])
                    stop = max(stop, extreme - v.trail * (a if v.trail_fixed else atr[j]))
            elif v.trail is None:
                if tp2 is not None and h[j] >= tp2:
                    px = o[j] if v.gap and o[j] > tp2 else tp2
                    realized += rem * (px - fill) / risk_plan
                    status, exit_px = "TP2", px
                    break
            else:
                extreme = max(extreme, h[j])
                stop = max(stop, extreme - v.trail * (a if v.trail_fixed else atr[j]))
            j += 1
        if status is None:
            break   # ακόμα ανοιχτό στο τέλος των δεδομένων
        fee_r = 2 * v.fee_pct / 100 * fill / risk_plan
        trades.append({"sym": pre["sym"], "i": i, "opened": idx[i] + pd.Timedelta(days=1), "j": j,
                       "closed": idx[j] + pd.Timedelta(days=1), "score": score[i], "entry": fill,
                       "plan_entry": entry_plan, "sl": sl, "risk": risk_plan, "atr": a, "status": status,
                       "gross": realized, "fee": fee_r, "r": realized - fee_r, "exit_px": exit_px,
                       "i_in": i + 1 if v.entry == "open" else i, "j1": j1, "p1": p1, "part": v.part,
                       "mom6": c[i] / c[i - 126] - 1, "vol": a / c[i]})
        i = max(i + 1, j)
        prev_long = bool(long_sig[i - 1])
    return trades


def stats(rs):
    rs = np.asarray(rs, dtype=float)
    if not len(rs):
        return {"n": 0, "win": 0.0, "avg": 0.0, "tot": 0.0, "pf": 0.0}
    wins, losses = rs[rs > 0].sum(), -rs[rs <= 0].sum()
    return {"n": len(rs), "win": 100 * (rs > 0).mean(), "avg": rs.mean(), "tot": rs.sum(),
            "pf": wins / losses if losses else np.inf}


_PRES = {}


def _get(sym):
    if sym not in _PRES:
        _PRES[sym] = pickle.load(open(PRE / f"{sym}.pkl", "rb"))
    return _PRES[sym]


def _work(args):
    v, sym = args
    return simulate_symbol(v, _get(sym))


def run(v: V, syms, pool) -> pd.DataFrame:
    parts = pool.map(_work, [(v, s) for s in syms])
    return pd.DataFrame([t for p in parts for t in p])
