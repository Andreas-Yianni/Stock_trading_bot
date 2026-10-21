"""Backtesting: τρέχει τη λογική του σκορ πάνω σε ιστορικά δεδομένα και μετράει πώς θα είχε πάει.

Χρησιμοποιεί ακριβώς τους ίδιους κανόνες με τα live σήματα (engine + journal.simulate), χωρίς
να «βλέπει» το μέλλον: σε κάθε κερί η ανάλυση γίνεται μόνο με όσα δεδομένα υπήρχαν τότε.
- SL/TP ελέγχονται σε μικρότερα κεριά (π.χ. 1h μέσα στο 4h), όπως και στο paper trading, ώστε να
  ξέρουμε τι χτυπήθηκε πρώτο. Όπου δεν υπάρχουν (Yahoo: ~2 χρόνια για 1h), στα κεριά του timeframe.
- Fear & Greed από το ιστορικό του (crypto). Οι ειδήσεις δεν μπαίνουν (δεν υπάρχει ιστορικό τους).
- Από το μεγαλύτερο timeframe μετράνε μόνο τα κεριά που είχαν κλείσει.
- Μία θέση τη φορά ανά σύμβολο: νέο σήμα μόνο όταν έχει κλείσει το προηγούμενο.
- Η περίοδος χωρίζεται σε «παλιά» (πρώτα 2/3) και «νέα» (τελευταίο 1/3) δεδομένα. Όταν αλλάζουμε
  κανόνες, μια βελτίωση μετράει μόνο αν φαίνεται και στα νέα δεδομένα.
"""
from dataclasses import dataclass, field

import pandas as pd

from . import engine, indicators, journal, levels, market, news
from .config import load_config

DEFAULT_DAYS = {"15m": 59, "1h": 365, "4h": 3 * 365, "1d": 5 * 365, "1w": 10 * 365}
FINE_TF = {"4h": "1h", "1d": "1h", "1w": "1d"}   # σε ποια κεριά ελέγχουμε SL/TP
WARMUP_BARS = 250   # για να «σταθεροποιηθεί» ο EMA200
HTF_WARMUP_BARS = 60
TEST_FRACTION = 1 / 3


@dataclass
class Trade:
    symbol: str
    opened: pd.Timestamp
    closed: pd.Timestamp
    side: str
    score: float
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    status: str          # SL, TP1, TP2, TRAIL, STOP (έξοδος τάσης), EXPIRED
    gross_r: float
    fee_r: float
    risk_frac: float     # τι ποσοστό του κεφαλαίου ρισκάρει (μετά το όριο θέσης)
    fine: bool           # αν το SL/TP ελέγχθηκε σε μικρότερα κεριά
    exit_at: pd.Timestamp | None = None   # το (μικρό) κερί όπου έκλεισε
    exit_price: float | None = None       # πού βγήκε το τελευταίο κομμάτι της θέσης
    reasons: list[str] = field(default_factory=list)   # τι έλεγαν οι δείκτες στην είσοδο
    risk_unit: float | None = None        # 1R σε τιμή (από το plan): η ποσότητα βγαίνει από αυτό

    @property
    def r(self) -> float:
        return self.gross_r - self.fee_r


def stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    rs = [t.r for t in sorted(trades, key=lambda t: t.closed)]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    cum = pd.Series(rs).cumsum()
    dd_r = float((cum - cum.cummax().clip(lower=0)).min())
    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return {
        "n": len(rs),
        "win_rate": 100 * len(wins) / len(rs),
        "total_r": float(sum(rs)),
        "avg_r": float(sum(rs) / len(rs)),
        "profit_factor": float(sum(wins) / -sum(losses)) if sum(losses) < 0 else float("inf"),
        "max_dd_r": min(dd_r, 0.0),
        "loss_streak": worst,
    }


@dataclass
class Result:
    asset: market.Asset
    timeframe: str
    start: pd.Timestamp
    end: pd.Timestamp
    bars: int
    trades: list[Trade]
    balance: float
    risk_pct: float
    fee_pct: float
    buy_hold_pct: float
    fine_tf: str | None = None
    with_fg: bool = False
    open_trade: dict | None = None
    equity: pd.Series = field(default_factory=pd.Series)
    buy_hold: pd.Series = field(default_factory=pd.Series)   # αξία κεφαλαίου αν απλά αγόραζες στην αρχή
    df: pd.DataFrame = field(default_factory=pd.DataFrame)   # κεριά + δείκτες της περιόδου (για charts)

    def stats(self, trades: list[Trade] | None = None) -> dict:
        return stats(self.trades if trades is None else trades)

    @property
    def split_at(self) -> pd.Timestamp:
        return split_at(self.start, self.end)

    @property
    def final_balance(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.balance

    @property
    def max_dd_pct(self) -> float:
        if len(self.equity) < 2:
            return 0.0
        return float((self.equity / self.equity.cummax() - 1).min() * 100)


def split_at(start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp:
    """Από εδώ και μετά είναι τα «νέα» δεδομένα (τελευταίο 1/3 της περιόδου)."""
    return start + (end - start) * (1 - TEST_FRACTION)


def _fine_history(asset, tf: str, days: int, daily: pd.DataFrame) -> tuple[pd.DataFrame | None, str | None]:
    fine_tf = FINE_TF.get(tf)
    if not fine_tf:
        return None, None
    try:
        fine = market.fetch_history(asset, fine_tf, days)
    except Exception:
        return None, None
    if asset.kind == "stock" and tf == "1d":
        fine = _match_daily(fine, daily)
    return fine, fine_tf


def _match_daily(fine: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Τα ωριαία κεριά του Yahoo ΔΕΝ είναι προσαρμοσμένα για μερίσματα, τα ημερήσια είναι (οι παλιές τιμές
    είναι χαμηλότερες κατά τα μερίσματα που ακολούθησαν, π.χ. JPM -6%, PFE -20% πριν από 2 χρόνια).
    Χωρίς διόρθωση ένα LONG «έπιανε» TP πιο εύκολα και ένα SHORT το SL: το backtest έδειχνε τα LONG
    πολύ καλύτερα (+0.32R αντί για +0.13R) και τα SHORT πολύ χειρότερα. Κλιμακώνουμε κάθε ωριαίο κερί
    με τον λόγο ημερήσιο / ωριαίο κλείσιμο της ίδιας ημέρας."""
    tz = "America/New_York"
    fday = fine.index.tz_convert(tz).normalize()
    dclose = pd.Series(daily["close"].to_numpy(), index=daily.index.tz_convert(tz).normalize())
    ratio = dclose.groupby(level=0).last() / fine["close"].groupby(fday).last()
    f = ratio.reindex(fday).ffill().bfill().fillna(1.0).to_numpy()
    out = fine.copy()
    for k in ("open", "high", "low", "close"):
        out[k] = out[k] * f
    return out


def _fg_at(fgh: pd.DataFrame | None, when: pd.Timestamp) -> dict | None:
    if fgh is None:
        return None
    k = int(fgh.index.searchsorted(when, side="right")) - 1
    if k < 0 or when - fgh.index[k] > pd.Timedelta(days=2):
        return None
    return {"value": int(fgh["value"].iloc[k]), "label": fgh["label"].iloc[k]}


def run(symbol: str, timeframe: str | None = None, days: int | None = None,
        intrabar: bool = True, with_fg: bool = True, overrides: dict | None = None) -> Result:
    """`overrides`: αλλαγές στο analysis του config μόνο για αυτό το backtest, π.χ. {"trend_filter": True}."""
    cfg = load_config()
    cfg["analysis"] = {**cfg["analysis"], **(overrides or {})}
    bt = cfg.get("backtest", {})
    fee_pct = float(bt.get("fee_pct", 0.1))
    max_days = int(bt.get("max_days", 30))
    tf = timeframe or cfg["analysis"]["default_timeframe"]
    days = int(days or DEFAULT_DAYS[tf])
    asset = market.resolve(symbol)

    bar = market.TF_DELTA[tf]
    warm_days = int(WARMUP_BARS * bar / pd.Timedelta(days=1)) + 1
    df = indicators.add_all(market.fetch_history(asset, tf, days + warm_days))
    if len(df) < WARMUP_BARS + 50:
        raise ValueError(f"Λίγα ιστορικά δεδομένα ({len(df)} κεριά) για backtest στο {tf}")

    htf_name = market.HIGHER_TF[tf]
    htf, htf_close = None, None
    if htf_name != tf:
        h_days = days + warm_days + int(HTF_WARMUP_BARS * market.TF_DELTA[htf_name] / pd.Timedelta(days=1))
        htf = indicators.add_all(market.fetch_history(asset, htf_name, h_days))
        htf_close = htf.index + market.TF_DELTA[htf_name]  # πότε κλείνει κάθε κερί

    fine, fine_tf = _fine_history(asset, tf, days + 2, df) if intrabar else (None, None)
    fine_bar = market.TF_DELTA[fine_tf] if fine_tf else None
    fgh = None
    if with_fg and asset.kind == "crypto":
        try:
            fgh = news.fear_greed_history()
        except Exception:
            pass

    acc = cfg["account"]
    balance = float(acc["balance"])
    equity_val = balance
    equity = {df.index[WARMUP_BARS]: balance}
    trades: list[Trade] = []
    open_trade = None
    close_times = df.index + bar

    trend_atr = engine.trend_atr(cfg)

    def evaluate(k: int):
        window = df.iloc[: k + 1]
        opened = close_times[k]
        h = None
        if htf is not None:
            h = htf.iloc[: int(htf_close.searchsorted(opened, side="right"))]
        factors = engine._trend_factors(window, h, htf_name)
        factors += engine._sentiment_factors(asset, [], _fg_at(fgh, opened))
        score, bias = engine.combine(factors, cfg)
        bias, _ = engine.trend_filter(bias, window.iloc[-1], cfg)
        return window, opened, factors, score, bias

    # Όπως το live bot: trade μόνο σε ΝΕΟ σήμα (η πλευρά άλλαξε από το προηγούμενο κερί) και όταν δεν
    # υπάρχει ανοιχτή θέση. Είσοδος στο άνοιγμα του επόμενου κεριού (μετοχές: Market στις 16:30),
    # με τα SL/TP και την ποσότητα του plan, που υπολογίστηκαν στο κλείσιμο.
    i = WARMUP_BARS
    prev_bias = evaluate(i - 1)[4]
    while i < len(df):
        window, opened, factors, score, bias = evaluate(i)
        fresh = bias != "NEUTRAL" and bias != prev_bias
        prev_bias = bias
        if not fresh:
            i += 1
            continue
        if i + 1 >= len(df):
            break   # το σήμα βγήκε στο τελευταίο κερί: η είσοδος είναι αύριο

        plan = engine._build_plan(bias, window, levels.find_levels(window), cfg)
        sign = 1 if bias == "LONG" else -1
        fill = float(df["open"].iloc[i + 1])
        if sign * (fill - plan.stop_loss) <= 0:
            i += 1   # άνοιξε ήδη πέρα από το stop loss: δεν μπαίνουμε
            continue
        expiry = opened + pd.Timedelta(days=max_days) if trend_atr is None else None
        use_fine = trend_atr is None and fine is not None and len(fine) and fine.index[0] <= opened
        if use_fine:
            candles, step = fine.iloc[fine.index.searchsorted(opened): fine.index.searchsorted(expiry)], fine_bar
        else:
            # Η έξοδος τάσης έχει μόνο stop (καμία αμφιβολία μέσα στο κερί) και θέλει το ATR κάθε κεριού
            end = len(df) if expiry is None else int(df.index.searchsorted(expiry))
            candles, step = df.iloc[i + 1: end], bar
        expired = expiry is not None and df.index[-1] + bar >= expiry   # τα δεδομένα φτάνουν μέχρι τη λήξη
        status, gross, when, exit_price = journal.simulate(
            bias, fill, plan.stop_loss, plan.tp1, plan.tp2, candles, trail=plan.trail, close_at_end=expired,
            trend_atr=trend_atr, risk=plan.risk_per_unit)

        if not status:
            if not expired:
                # Τα δεδομένα τελειώνουν πριν κλείσει: είναι ακόμα «ανοιχτό»
                open_trade = {"side": bias, "opened": opened, "entry": fill, "score": score,
                              "stop_loss": plan.stop_loss, "tp1": plan.tp1, "tp2": plan.tp2}
                if trend_atr is not None and len(candles):
                    # Το stop σήμερα = max(αρχικό SL, max(υψηλότερο high μέχρι τότε − k·ATR)) σε όλη τη διαδρομή
                    if sign > 0:
                        now_stop = max(plan.stop_loss, float((candles.high.cummax() - trend_atr * candles.atr).max()))
                    else:
                        now_stop = min(plan.stop_loss, float((candles.low.cummin() + trend_atr * candles.atr).min()))
                    open_trade |= {"stop_now": now_stop, "last": float(candles.close.iloc[-1]),
                                   "r_now": sign * (float(candles.close.iloc[-1]) - fill) / plan.risk_per_unit}
                break
            status, gross, when, exit_price = "EXPIRED", 0.0, df.index[i], fill

        # Προμήθεια exchange σε είσοδο + έξοδο, εκφρασμένη σε R
        fee_r = 2 * fee_pct / 100 * fill / plan.risk_per_unit
        risk_frac = plan.risk_amount / balance
        t = Trade(asset.symbol, opened, when + step, bias, score, fill, plan.stop_loss, plan.tp1, plan.tp2,
                  status, gross, fee_r, risk_frac, bool(use_fine), when, exit_price,
                  [f"{'🟢' if f.score > 0.1 else '🔴' if f.score < -0.1 else '⚪'} {f.name}: {f.note}" for f in factors],
                  plan.risk_per_unit)
        trades.append(t)
        equity_val *= 1 + t.r * risk_frac
        equity[t.closed] = equity_val

        # Νέο σήμα μπορεί να βγει στο κλείσιμο του κεριού όπου έκλεισε το trade
        i = max(i + 1, int(df.index.searchsorted(when, side="right")) - 1)
        prev_bias = evaluate(i - 1)[4]

    start_px, end_px = float(df["close"].iloc[WARMUP_BARS]), float(df["close"].iloc[-1])
    return Result(
        asset=asset, timeframe=tf, start=df.index[WARMUP_BARS], end=df.index[-1],
        bars=len(df) - WARMUP_BARS, trades=trades, balance=balance,
        risk_pct=float(acc["risk_per_trade_pct"]), fee_pct=fee_pct,
        buy_hold_pct=(end_px / start_px - 1) * 100, fine_tf=fine_tf, with_fg=fgh is not None,
        open_trade=open_trade, equity=pd.Series(equity).sort_index(),
        buy_hold=balance * df["close"].iloc[WARMUP_BARS:] / start_px,
        df=df.iloc[WARMUP_BARS:][["open", "high", "low", "close", "volume", "ema20", "ema50", "ema200"]],
    )


def _day(ts: pd.Timestamp, kind: str) -> pd.Timestamp:
    """Ημερομηνία συνεδρίασης (μετοχές: ώρα Νέας Υόρκης), χωρίς ώρα, για να ευθυγραμμιστούν όλα τα σύμβολα."""
    return ts.tz_convert("America/New_York" if kind == "stock" else "UTC").normalize().tz_localize(None)


def portfolio(results: list[Result], balance: float, risk_pct: float, max_pos_pct: float) -> dict:
    """ΕΝΑΣ λογαριασμός για όλα τα σύμβολα μαζί, όπως θα τα έπαιρνες στην πράξη: κάθε trade μπαίνει μόνο
    αν υπάρχουν μετρητά (χωρίς margin), με ρίσκο risk_pct% του τρέχοντος κεφαλαίου και θέση έως
    max_pos_pct%. Αν την ίδια μέρα βγουν πολλά σήματα, πρώτα το μεγαλύτερο σκορ. Αποτίμηση κάθε μέρα
    στο κλείσιμο. Το «μέσο R ανά trade» δεν λέει πόσο αποδίδουν τα χρήματα που δεσμεύει ένα trade:
    αυτό εδώ λέει (CAGR, μέγιστη πτώση), και συγκρίνεται με το να είχες απλά αγοράσει και κρατήσει."""
    closes, entries, exits = {}, {}, {}
    for r in results:
        c = r.df["close"].copy()
        c.index = [_day(x, r.asset.kind) for x in c.index]
        closes[r.asset.symbol] = c[~c.index.duplicated(keep="last")]
        for t in r.trades:
            k = min(int(r.df.index.searchsorted(t.opened)), len(r.df) - 1)
            j = max(k, int(r.df.index.searchsorted(t.exit_at, side="right")) - 1)
            entries.setdefault(_day(r.df.index[k], r.asset.kind), []).append(t)
            exits[id(t)] = _day(r.df.index[j], r.asset.kind)
    cal = sorted(set().union(*[set(c.index) for c in closes.values()])) if closes else []
    cash, eq, pos, curve, taken, skipped = balance, balance, [], [], 0, 0
    for d in cal:
        for t in sorted(entries.get(d, []), key=lambda t: -abs(t.score)):
            if any(p["t"].symbol == t.symbol for p in pos):
                skipped += 1
                continue
            unit = t.risk_unit or abs(t.entry - t.stop_loss)
            qty = min(eq * risk_pct / 100 / unit, eq * max_pos_pct / 100 / t.entry)
            if qty * t.entry > cash:
                if cash < 0.5 * qty * t.entry:
                    skipped += 1
                    continue
                qty = cash / t.entry
            cash -= qty * t.entry
            taken += 1
            pos.append({"t": t, "qty": qty, "last": t.entry, "unit": unit})
        value, keep = 0.0, []
        for p in pos:
            t, sign = p["t"], (1 if p["t"].side == "LONG" else -1)
            if d >= exits[id(t)]:
                cash += p["qty"] * t.entry + t.r * p["qty"] * p["unit"]
                continue
            px = closes[t.symbol].get(d)
            if px is not None and px == px:
                p["last"] = float(px)
            value += p["qty"] * (t.entry + sign * (p["last"] - t.entry))
            keep.append(p)
        pos = keep
        eq = cash + value
        curve.append(eq)
    s = pd.Series(curve, index=pd.DatetimeIndex(cal), dtype=float)
    if len(s) < 2:
        return {"curve": s, "cagr": 0.0, "dd": 0.0, "n": taken, "skipped": skipped, "years": 0.0}
    years = (s.index[-1] - s.index[0]).days / 365.25
    return {"curve": s, "cagr": 100 * ((s.iloc[-1] / balance) ** (1 / years) - 1) if years > 0 else 0.0,
            "dd": 100 * float((s / s.cummax() - 1).min()), "n": taken, "skipped": skipped, "years": years}


def run_many(symbols: list[str], timeframe: str | None = None, days: int | None = None,
             progress=None, overrides: dict | None = None) -> tuple[list[Result], list[tuple[str, str]]]:
    """Backtest σε πολλά σύμβολα. Επιστρέφει (αποτελέσματα, [(σύμβολο, σφάλμα)])."""
    results, errors = [], []
    for n, sym in enumerate(symbols):
        try:
            results.append(run(sym, timeframe, days, overrides=overrides))
        except Exception as e:
            errors.append((sym, str(e)))
        if progress:
            progress((n + 1) / len(symbols))
    return results, errors
