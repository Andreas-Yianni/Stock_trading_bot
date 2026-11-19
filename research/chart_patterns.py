"""Classic bullish chart patterns (triangles, flags, wedges, double/triple bottom, inverse head & shoulders)
as causal entry signals, plus a small simulator with each pattern's own stop and measured-move target.

Causal: a swing pivot at bar j is only "known" at bar j + K (it needs K bars on each side), and a pattern
fires only on the bar whose CLOSE breaks out of it. Entry = next bar's open (like the live bot).
Definitions are a mechanical approximation of what traders draw by eye; that is unavoidable in a test.
"""
import numpy as np
import pandas as pd

K = 5            # pivot strength on daily bars
MAX_AGE = 60     # pattern pivots must be within this many bars
PATTERNS = ["double_bottom", "triple_bottom", "inv_hs", "asc_triangle", "sym_triangle", "falling_wedge",
            "bull_flag"]
NAMES = {"double_bottom": "Double bottom", "triple_bottom": "Triple bottom", "inv_hs": "Inverted H&S",
         "asc_triangle": "Ascending triangle", "sym_triangle": "Bullish symmetrical triangle",
         "falling_wedge": "Falling wedge", "bull_flag": "Bull flag"}


def pivots(x: np.ndarray, kind: str, k: int = K) -> np.ndarray:
    """Indices j where x[j] is the max (min) of x[j-k..j+k]."""
    s = pd.Series(x)
    w = s.rolling(2 * k + 1, center=True)
    ext = w.max() if kind == "high" else w.min()
    return np.flatnonzero((s == ext).to_numpy())


def _line(p1, p2, i):
    (x1, y1), (x2, y2) = p1, p2
    return y1 + (y2 - y1) * (i - x1) / (x2 - x1)


def detect(df: pd.DataFrame) -> dict[str, list[tuple[int, float, float]]]:
    """pattern -> [(bar i of the breakout close, stop, target)], all known at the close of bar i."""
    h, l, c, a = (df[k].to_numpy() for k in ("high", "low", "close", "atr"))
    ph, pl = pivots(h, "high"), pivots(l, "low")
    out = {p: [] for p in PATTERNS}
    n = len(df)
    for i in range(250, n):
        atr = a[i]
        if not atr == atr or atr <= 0:
            continue
        kh = ph[ph + K <= i]
        kl = pl[pl + K <= i]
        kh = kh[kh >= i - MAX_AGE]
        kl = kl[kl >= i - MAX_AGE]
        H = [(j, h[j]) for j in kh]
        L = [(j, l[j]) for j in kl]
        tol = 0.5 * atr

        def cross(level_now, level_prev):
            return c[i] > level_now and c[i - 1] <= level_prev

        def neck(j0, j1):
            between = [y for j, y in H if j0 < j < j1]
            return max(between) if between else None

        # Double bottom: two lows within tol, close breaks the high between them
        if len(L) >= 2:
            (j1, y1), (j2, y2) = L[-2], L[-1]
            nk = neck(j1, j2)
            if abs(y1 - y2) <= tol and nk is not None and cross(nk, nk) and (len(L) < 3 or abs(L[-3][1] - y2) > tol):
                lo = min(y1, y2)
                out["double_bottom"].append((i, lo - 0.25 * atr, nk + (nk - lo)))
        # Triple bottom
        if len(L) >= 3:
            ys = [y for _, y in L[-3:]]
            nk = neck(L[-3][0], L[-1][0])
            if max(ys) - min(ys) <= 1.5 * tol and nk is not None and cross(nk, nk):
                lo = min(ys)
                out["triple_bottom"].append((i, lo - 0.25 * atr, nk + (nk - lo)))
        # Inverted head & shoulders: shoulders within 1 ATR, head clearly lower; neckline = higher high between
        if len(L) >= 3:
            (s1j, s1), (hj, hd), (s2j, s2) = L[-3:]
            nk = neck(s1j, s2j)
            if hd < min(s1, s2) - tol and abs(s1 - s2) <= 2 * tol and nk is not None and cross(nk, nk):
                out["inv_hs"].append((i, s2 - 0.25 * atr, nk + (nk - hd)))
        # Ascending triangle: flat highs, rising lows, close above the flat top
        if len(H) >= 2 and len(L) >= 2:
            (h1j, h1), (h2j, h2) = H[-2], H[-1]
            (l1j, l1), (l2j, l2) = L[-2], L[-1]
            top = max(h1, h2)
            if abs(h1 - h2) <= tol and l2 > l1 + 0.5 * tol and l1j < h2j and l2j > h1j and cross(top, top):
                out["asc_triangle"].append((i, l2 - 0.25 * atr, top + (top - l1)))
        # Symmetrical triangle in an uptrend: lower highs, higher lows, close breaks the upper line
        if len(H) >= 2 and len(L) >= 2:
            (h1j, h1), (h2j, h2) = H[-2], H[-1]
            (l1j, l1), (l2j, l2) = L[-2], L[-1]
            if h2 < h1 - tol and l2 > l1 + tol and l1j < h2j and l2j > h1j and c[h1j] > df["ema50"].iat[h1j]:
                up_now, up_prev = _line((h1j, h1), (h2j, h2), i), _line((h1j, h1), (h2j, h2), i - 1)
                if cross(up_now, up_prev):
                    out["sym_triangle"].append((i, l2 - 0.25 * atr, c[i] + (h1 - l1)))
        # Falling wedge: 3 lower highs and 3 lower lows, converging; close breaks the upper line
        if len(H) >= 3 and len(L) >= 3:
            hs, ls = H[-3:], L[-3:]
            if all(hs[k][1] > hs[k + 1][1] for k in range(2)) and all(ls[k][1] > ls[k + 1][1] for k in range(2)):
                sh = (hs[2][1] - hs[0][1]) / (hs[2][0] - hs[0][0])
                sl_ = (ls[2][1] - ls[0][1]) / (ls[2][0] - ls[0][0])
                if sh < sl_ < 0:
                    up_now, up_prev = _line(hs[1], hs[2], i), _line(hs[1], hs[2], i - 1)
                    if cross(up_now, up_prev):
                        out["falling_wedge"].append((i, ls[2][1] - 0.25 * atr, c[i] + (hs[0][1] - ls[0][1])))
        # Bull flag: pole >= 4 ATR in <= 10 bars, then 5-15 bars of tight, flat/down drift; close above flag high
        for m in range(5, 16):
            f0 = i - m
            if f0 - 10 < 0:
                break
            fh, fl = h[f0:i].max(), l[f0:i].min()
            pole_lo = l[f0 - 10:f0 + 1].min()
            pole = h[f0] - pole_lo
            if pole >= 4 * atr and fh <= h[f0] + 0.25 * atr and (fh - fl) <= 0.5 * pole and c[i - 1] <= c[f0] \
                    and c[i] > fh and c[i - 1] <= fh:
                out["bull_flag"].append((i, fl - 0.25 * atr, c[i] + pole))
                break
    return out


def simulate(sym: str, df: pd.DataFrame, events, exit_mode: str, score=None, thr=None,
             trail=6.0, fee_pct=0.1, min_risk_atr=0.5):
    """Trades from (i, stop, target) events. exit_mode 'target': stop or measured-move target;
    'trend': same initial stop, then max(stop, highest high since entry - trail*ATR) (the bot's exit).
    Fill at next open, gaps through stop/target filled at the open, worst case if both in one bar."""
    o, h, l, c, a = (df[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    idx = df.index
    n = len(df)
    trades = []
    busy_until = -1
    for i, sl, tp in events:
        if i <= busy_until or i + 1 >= n:
            continue
        if score is not None and not score[i] >= thr:
            continue
        plan = c[i]
        sl = min(sl, plan - min_risk_atr * a[i])
        risk = plan - sl
        fill = o[i + 1]
        if fill <= sl or (exit_mode == "target" and fill >= tp):
            continue
        stop, ext, status, px = sl, None, None, None
        j = i + 1
        while j < n:
            if l[j] <= stop:
                px = o[j] if o[j] < stop else stop
                status = "SL" if stop <= sl else "STOP"
                break
            if exit_mode == "target" and h[j] >= tp:
                px = o[j] if o[j] > tp else tp
                status = "TP"
                break
            if exit_mode == "trend":
                ext = h[j] if ext is None else max(ext, h[j])
                stop = max(stop, ext - trail * a[j])
            j += 1
        if status is None:
            break
        fee_r = 2 * fee_pct / 100 * fill / risk
        r = (px - fill) / risk - fee_r
        trades.append({"sym": sym, "i": i, "opened": idx[i] + pd.Timedelta(days=1), "j": j, "i_in": i + 1,
                       "j1": None, "p1": None, "part": 0.0, "score": score[i] if score is not None else 0.0,
                       "entry": fill, "risk": risk, "r": r, "status": status})
        busy_until = j
    return trades
