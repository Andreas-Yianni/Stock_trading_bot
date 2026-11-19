"""Ένας λογαριασμός (χωρίς margin, αποτίμηση κάθε μέρα) για τα trades του research/lab.py, και benchmarks
(SPY, ίσα βάρη). Ίδια λογική με το tradebot.backtest.portfolio."""
import numpy as np
import pandas as pd

_CL = {}


def closes(pre):
    s = pre["sym"]
    if s not in _CL:
        df = pre["df"]
        _CL[s] = (df.index.tz_convert("America/New_York").normalize().tz_localize(None), df["close"].to_numpy())
    return _CL[s]


def spy_ok(pres, n=200):
    """date -> SPY close > SMA(n) at that date's close (known at signal time)."""
    dates, c = closes(pres["SPY"])
    s = pd.Series(c, index=dates)
    return (s > s.rolling(n).mean())


def simulate(T, pres, start, end, balance=100_000.0, risk_pct=1.0, max_pos_pct=25.0,
             max_open=None, priority="score", ascending=False, market=None, park=None, park_fee_pct=0.0,
             size_on="plan", max_gap_r=None):
    """park="SPY": idle cash is held in that ETF (bought at each day's close, sold as needed to fund
    entries), so the account stays ~fully invested. park_fee_pct = cost per SPY buy/sell.
    size_on="fill": qty from the actual fill (next open) - SL instead of the plan's entry - SL.
    max_gap_r: skip the trade if it opens more than this many plan-R above the plan entry."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    T = T.copy()
    ent, ex, d1, dsig = [], [], [], []
    for t in T.itertuples():
        dates, _ = closes(pres[t.sym])
        ent.append(dates[t.i_in]); ex.append(dates[t.j]); dsig.append(dates[t.i])
        d1.append(dates[int(t.j1)] if t.j1 is not None and t.j1 == t.j1 else pd.NaT)
    T["d_in"], T["d_out"], T["d1"], T["d_sig"] = ent, ex, d1, dsig
    T = T[(T.d_in >= start) & (T.d_in < end)]
    if market is not None:
        ok = market.reindex(T.d_sig).fillna(False).to_numpy()
        T = T[ok]
    spy_dates, _ = closes(pres["SPY"])
    cal = spy_dates[(spy_dates >= start) & (spy_dates < end)]
    by_day = {d: g.sort_values(priority, ascending=ascending) for d, g in T.groupby("d_in")}
    cl = {}
    for s in T.sym.unique():
        dates, c = closes(pres[s])
        cl[s] = pd.Series(c, index=dates)
    cash = balance
    park_u = 0.0
    park_px = None
    if park is not None:
        pd_, pc_ = closes(pres[park])
        park_px = pd.Series(pc_, index=pd_)
    pos = []
    eq_prev = balance
    curve, expo = [], []
    taken = skipped = 0
    for d in cal:
        ppx = float(park_px.get(d, np.nan)) if park is not None else np.nan
        if ppx != ppx and park is not None:
            ppx = float(park_px[park_px.index <= d].iloc[-1])
        if d in by_day:
            for t in by_day[d].itertuples():
                if (max_open is not None and len(pos) >= max_open) or any(p["sym"] == t.sym for p in pos):
                    skipped += 1
                    continue
                if max_gap_r is not None and (t.entry - t.plan_entry) / t.risk > max_gap_r:
                    skipped += 1
                    continue
                rps = max(t.entry - t.sl, 1e-9) if size_on == "fill" else t.risk
                qty = eq_prev * risk_pct / 100 / rps
                cap = eq_prev * max_pos_pct / 100
                if qty * t.entry > cap:
                    qty = cap / t.entry
                avail = cash + (park_u * ppx * (1 - park_fee_pct / 100) if park is not None else 0.0)
                if qty * t.entry > avail:
                    if avail < 0.5 * qty * t.entry:
                        skipped += 1
                        continue
                    qty = avail / t.entry
                cash -= qty * t.entry
                if cash < 0 and park is not None:
                    sell = min(park_u, -cash / (ppx * (1 - park_fee_pct / 100)))
                    park_u -= sell
                    cash += sell * ppx * (1 - park_fee_pct / 100)
                taken += 1
                pos.append({"sym": t.sym, "qty": qty, "fill": t.entry, "part": t.part, "p1": t.p1, "d1": t.d1,
                            "d_out": t.d_out, "pnl": t.r * qty * t.risk, "done1": False, "last": t.entry})
        mv = 0.0
        keep = []
        for p in pos:
            if p["d1"] is not pd.NaT and not p["done1"] and d >= p["d1"] and p["part"] > 0:
                cash += p["qty"] * p["part"] * p["p1"]
                p["done1"] = True
            rem = 1 - (p["part"] if p["done1"] else 0)
            if d >= p["d_out"]:
                partial_pnl = p["qty"] * p["part"] * (p["p1"] - p["fill"]) if p["done1"] else 0.0
                cash += p["qty"] * rem * p["fill"] + (p["pnl"] - partial_pnl)
                continue
            px = cl[p["sym"]].get(d, np.nan)
            if px == px:
                p["last"] = px
            mv += p["qty"] * rem * p["last"]
            keep.append(p)
        pos = keep
        if park is not None and cash > 0:
            park_u += cash * (1 - park_fee_pct / 100) / ppx
            cash = 0.0
        eq_prev = cash + mv + (park_u * ppx if park is not None else 0.0)
        curve.append(eq_prev)
        expo.append(mv / eq_prev if eq_prev > 0 else 0)
    s = pd.Series(curve, index=cal)
    yrs = (cal[-1] - cal[0]).days / 365.25
    cagr = 100 * ((s.iloc[-1] / balance) ** (1 / yrs) - 1)
    dd = 100 * float((s / s.cummax() - 1).min())
    rets = s.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    return {"cagr": cagr, "dd": dd, "sharpe": sharpe, "n": taken, "skipped": skipped,
            "expo": 100 * float(np.mean(expo)), "curve": s}


def bh(pres, sym, start, end):
    dates, c = closes(pres[sym])
    s = pd.Series(c, index=dates)
    s = s[(s.index >= pd.Timestamp(start)) & (s.index < pd.Timestamp(end))]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    rets = s.pct_change().dropna()
    return {"cagr": 100 * ((s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1), "dd": 100 * float((s / s.cummax() - 1).min()),
            "sharpe": float(rets.mean() / rets.std() * np.sqrt(252))}


def ew_bh(pres, syms, start, end):
    """Equal-weight buy & hold (no rebalancing) of the symbols that already trade at `start`."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    paths = []
    for sym in syms:
        dates, c = closes(pres[sym])
        s = pd.Series(c, index=dates)
        s = s[(s.index >= start) & (s.index < end)]
        if len(s) and s.index[0] <= start + pd.Timedelta(days=10):
            paths.append(s / s.iloc[0])
    p = pd.concat(paths, axis=1).ffill().mean(axis=1)
    yrs = (p.index[-1] - p.index[0]).days / 365.25
    rets = p.pct_change().dropna()
    return {"cagr": 100 * (p.iloc[-1] ** (1 / yrs) - 1), "dd": 100 * float((p / p.cummax() - 1).min()),
            "sharpe": float(rets.mean() / rets.std() * np.sqrt(252)), "n": len(paths)}
