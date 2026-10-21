"""Η «καρδιά» του bot: συνδυάζει δείκτες, επίπεδα και ειδήσεις σε σκορ και προτεινόμενο trade plan."""
from dataclasses import dataclass, field

import pandas as pd

from . import events, indicators, levels, market, news
from .config import load_config


@dataclass
class Factor:
    name: str
    score: float    # -1 (bearish) έως +1 (bullish)
    weight: float
    note: str


@dataclass
class TradePlan:
    side: str                 # "LONG" ή "SHORT"
    entry: float
    stop_loss: float
    tp1: float | None          # None στην έξοδο «τάσης» (χωρίς σταθερούς στόχους)
    tp2: float | None
    risk_per_unit: float
    qty: float
    position_value: float
    risk_amount: float
    alt_entry: float | None = None
    trail: float | None = None     # trailing stop μετά το TP1 (απόσταση σε τιμή), αν είναι ενεργό
    trend_atr: float | None = None  # έξοδος «τάσης»: stop = υψηλότερο σημείο − trend_atr × ATR (ανεβαίνει μόνο)
    notes: list[str] = field(default_factory=list)

    @property
    def rr_tp1(self) -> float | None:
        return abs(self.tp1 - self.entry) / self.risk_per_unit if self.tp1 is not None else None

    @property
    def rr_tp2(self) -> float | None:
        return abs(self.tp2 - self.entry) / self.risk_per_unit if self.tp2 is not None else None


@dataclass
class Analysis:
    asset: market.Asset
    timeframe: str
    price: float
    df: pd.DataFrame
    levels: dict
    factors: list[Factor]
    score: float              # -100 έως +100
    bias: str                 # "LONG", "SHORT", "NEUTRAL"
    plan: TradePlan | None
    news: list[news.NewsItem]
    fear_greed: dict | None
    warnings: list[str]
    events: list[events.Event] = field(default_factory=list)       # επερχόμενα γεγονότα (CPI, Fed, earnings)
    market_news: list[news.NewsItem] = field(default_factory=list)  # γενικές ειδήσεις αγοράς (μόνο ενημέρωση)

    @property
    def confidence(self) -> str:
        a = abs(self.score)
        return "Υψηλή" if a >= 60 else "Μέτρια" if a >= 40 else "Χαμηλή"


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _trend_factors(df: pd.DataFrame, htf: pd.DataFrame | None, htf_name: str) -> list[Factor]:
    last = df.iloc[-1]
    out = []

    # Τα βάρη και οι κανόνες εδώ στηρίζονται στη μελέτη του tradebot/research.py (20 σύμβολα, 4h + 1d)
    t = (0.5 if last.close > last.ema200 else -0.5) + (0.5 if last.ema20 > last.ema50 else -0.5)
    strength = "δυνατή" if last.adx >= 25 else "αδύναμη" if last.adx < 20 else "μέτρια"
    side = "πάνω" if last.close > last.ema200 else "κάτω"
    cross = "EMA20 > EMA50" if last.ema20 > last.ema50 else "EMA20 < EMA50"
    note = f"Τιμή {side} από EMA200, {cross}, ADX {last.adx:.0f} ({strength} τάση)"
    if last.adx < 20 and t:
        # Με αδύναμο ADX η «τάση» ιστορικά δεν πρόβλεπε τίποτα
        t, note = 0.0, note + ": δεν μετράει"
    out.append(Factor("Τάση", t, 3, note))

    if htf is not None and len(htf) > 50:
        h = htf.iloc[-1]
        ht = (0.5 if h.close > h.ema50 else -0.5) + (0.5 if h.ema20 > h.ema50 else -0.5)
        word = "ανοδική" if ht > 0 else "καθοδική" if ht < 0 else "ουδέτερη"
        out.append(Factor(f"Τάση {htf_name}", ht, 1, f"Η τάση στο μεγαλύτερο timeframe ({htf_name}) είναι {word}"))

    prev = df.iloc[-2]
    m = (0.5 if last.macd_hist > 0 else -0.5) + (0.5 if last.macd_hist > prev.macd_hist else -0.5)
    out.append(Factor("Momentum (MACD)", m, 2,
                      f"MACD histogram {'θετικό' if last.macd_hist > 0 else 'αρνητικό'} και "
                      f"{'ανεβαίνει' if last.macd_hist > prev.macd_hist else 'πέφτει'}"))

    r = last.rsi
    if r < 30:
        rs, note = 0.8, f"RSI {r:.0f}: υπερπουλημένο, πιθανή αναπήδηση"
    elif r > 70:
        # Ιστορικά, μετά από RSI > 70 η τιμή συνέχιζε να ανεβαίνει: είναι σημάδι δύναμης, όχι κορυφής
        rs, note = 0.8, f"RSI {r:.0f}: δυνατό momentum, οι αγοραστές πιέζουν (σε τάση το RSI μένει ψηλά)"
    elif r >= 50:
        rs, note = 0.0, f"RSI {r:.0f}: ουδέτερο"
    else:
        rs, note = -0.3, f"RSI {r:.0f}: οι πωλητές έχουν το πάνω χέρι"
    out.append(Factor("RSI", rs, 1.5, note))

    if last.vol_avg20 and last.volume > 1.5 * last.vol_avg20:
        up = last.close > last.open
        ratio = last.volume / last.vol_avg20
        if up:
            out.append(Factor("Όγκος", 0.7, 1.5, f"Αυξημένος όγκος ({ratio:.1f}x του μέσου) σε πράσινο κερί: "
                                                  f"αγοραστές μπαίνουν δυναμικά"))
        else:
            # Ο μεγάλος όγκος σε κόκκινο κερί ιστορικά δεν έδειχνε συνέχεια της πτώσης
            out.append(Factor("Όγκος", 0.0, 1.5, f"Αυξημένος όγκος ({ratio:.1f}x) σε κόκκινο κερί: "
                                                  f"συχνά πανικός που δεν συνεχίζεται"))
    else:
        out.append(Factor("Όγκος", 0.0, 1.5, "Κανονικός όγκος, χωρίς επιβεβαίωση"))
    return out


def _sentiment_factors(asset, items, fg) -> list[Factor]:
    out = []
    s = news.news_score(items)
    if s is not None:
        mood = "θετικό" if s > 0.1 else "αρνητικό" if s < -0.1 else "ουδέτερο"
        out.append(Factor("Ειδήσεις", _clip(s * 2), 1.5, f"{len(items)} πρόσφατες ειδήσεις, κλίμα {mood} ({s:+.2f})"))
    if fg and asset.kind == "crypto":
        v = fg["value"]
        # Αντίθετη λογική: ακραίος φόβος συχνά = ευκαιρία, ακραία απληστία = κίνδυνος
        fs = 0.6 if v <= 25 else -0.6 if v >= 75 else 0.0
        out.append(Factor("Fear & Greed", fs, 0.5, f"Δείκτης {v}/100 ({fg['label']})"))
    return out


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if x >= 1 else f"{x:.6g}"


def trend_atr(cfg: dict) -> float | None:
    """Πόσα ATR κάτω από το υψηλότερο σημείο μπαίνει το stop στην έξοδο «τάσης» (None = σταθεροί στόχοι TP1/TP2)."""
    a = cfg["analysis"]
    return float(a.get("trend_atr", 6)) if a.get("exit_mode") == "trend" else None


def _build_plan(side: str, df: pd.DataFrame, lv: dict, cfg: dict) -> TradePlan:
    last = df.iloc[-1]
    entry, a = float(last.close), float(last.atr)
    sign = 1 if side == "LONG" else -1
    protect = lv["support"] if side == "LONG" else lv["resistance"]
    target = lv["resistance"] if side == "LONG" else lv["support"]
    notes = []

    # Stop loss: λίγο πέρα από το πιο κοντινό επίπεδο που απέχει 1-3 ATR, αλλιώς 1.5 ATR
    sl = entry - sign * 1.5 * a
    for level, _touches in protect:
        candidate = level - sign * 0.25 * a
        dist = abs(entry - candidate)
        if 1.0 * a <= dist <= 3.0 * a:
            sl = candidate
            notes.append(f"Stop loss πίσω από {'στήριξη' if side == 'LONG' else 'αντίσταση'} στο {_fmt(level)}")
            break
    else:
        notes.append("Stop loss στα 1.5 ATR (δεν βρέθηκε κατάλληλο επίπεδο κοντά)")

    risk = abs(entry - sl)
    min_rr = cfg["analysis"]["min_reward_risk"]
    tp1 = entry + sign * min_rr * risk
    tp2 = entry + sign * 3 * risk
    trail = None
    k = trend_atr(cfg)
    if k is not None:
        tp1 = tp2 = None
        notes.append(f"Χωρίς take profit: η θέση μένει ανοιχτή όσο συνεχίζει η τάση. Κάθε πρωί το bot σου λέει "
                     f"πού να ανεβάσεις το stop: {k:g} ATR (σήμερα {_fmt(k * a)}) κάτω από το υψηλότερο σημείο "
                     f"από την είσοδο. Το stop δεν κατεβαίνει ποτέ.")
        notes.append("Περίπου 7 στα 10 trades κλείνουν με ζημιά (το πολύ όσο το ρίσκο). Τα λίγα που πετυχαίνουν "
                     "κερδίζουν πολλές φορές το ρίσκο: μην κλείνεις νωρίς τα κερδισμένα.")
    elif cfg["analysis"].get("exit_mode") == "trailing":
        trail = float(cfg["analysis"].get("trail_atr", 3)) * a
        notes.append(f"Μετά το TP1: αντί για σταθερό TP2, το stop ακολουθεί την τιμή σε απόσταση "
                     f"{_fmt(trail)} (trailing stop), για να «τρέξει» το κέρδος αν συνεχίσει η κίνηση")

    for level, _ in target if tp1 is not None else []:
        if sign * (level - entry) > 0 and sign * (tp1 - level) > 0:
            word = "Αντίσταση" if side == "LONG" else "Στήριξη"
            notes.append(f"{word} στο {_fmt(level)} πριν το TP1: η τιμή μπορεί να κολλήσει εκεί, "
                         f"σκέψου να κλείσεις μέρος της θέσης")
            break

    alt = None
    if sign * (entry - last.ema20) > 1.5 * a:
        if sign * (last.ema20 - sl) > 0.5 * a:
            alt = float(last.ema20)
            notes.append(f"Η τιμή έχει απομακρυνθεί πολύ από τον EMA20, πιο ασφαλής είσοδος σε pullback κοντά στο {_fmt(alt)}")
        else:
            notes.append("Η τιμή έχει απομακρυνθεί πολύ από τον EMA20: υπάρχει ρίσκο αναπήδησης, μην κυνηγάς την τιμή")

    acc = cfg["account"]
    risk_amount = acc["balance"] * acc["risk_per_trade_pct"] / 100
    qty = risk_amount / risk
    max_value = acc["balance"] * acc["max_position_pct"] / 100
    if qty * entry > max_value:
        qty = max_value / entry
        notes.append(f"Η θέση περιορίστηκε στο {acc['max_position_pct']}% του κεφαλαίου, "
                     f"άρα το πραγματικό ρίσκο είναι μικρότερο")
    if side == "SHORT":
        notes.append("SHORT = κερδίζεις αν πέσει η τιμή. Θέλει margin/futures (crypto) ή short selling (μετοχές), "
                     "με μεγαλύτερο ρίσκο. Για αρχάριους, καλύτερα απλά να μην αγοράσεις.")

    return TradePlan(side, entry, sl, tp1, tp2, risk, qty, qty * entry, qty * risk,
                     alt_entry=alt, notes=notes, trail=trail, trend_atr=k)


def combine(factors: list[Factor], cfg: dict) -> tuple[float, str]:
    """Σταθμισμένο σκορ (-100..+100) και bias. Το χρησιμοποιεί και το backtest."""
    score = 100 * sum(f.score * f.weight for f in factors) / sum(f.weight for f in factors)
    threshold = cfg["analysis"]["min_score_for_signal"]
    bias = "LONG" if score >= threshold else "SHORT" if score <= -threshold else "NEUTRAL"
    return score, bias


def trend_filter(bias: str, last: pd.Series, cfg: dict) -> tuple[str, str | None]:
    """LONG μόνο πάνω από τον EMA200, SHORT μόνο κάτω (αν είναι ενεργό στο config)."""
    if bias == "SHORT" and cfg["analysis"].get("long_only"):
        return "NEUTRAL", "Το σκορ έδειχνε SHORT, αλλά παίρνουμε μόνο LONG (τα SHORT σε μετοχές έχαναν στο backtest)"
    if not cfg["analysis"].get("trend_filter") or bias == "NEUTRAL":
        return bias, None
    if bias == "LONG" and last.close < last.ema200:
        return "NEUTRAL", "Το σκορ έδειχνε LONG, αλλά η τιμή είναι κάτω από τον EMA200 (μακροπρόθεσμη πτώση): δεν προτείνεται"
    if bias == "SHORT" and last.close > last.ema200:
        return "NEUTRAL", "Το σκορ έδειχνε SHORT, αλλά η τιμή είναι πάνω από τον EMA200 (μακροπρόθεσμη άνοδος): δεν προτείνεται"
    return bias, None


def analyze(symbol: str, timeframe: str | None = None, with_news: bool = True) -> Analysis:
    cfg = load_config()
    tf = timeframe or cfg["analysis"]["default_timeframe"]
    asset = market.resolve(symbol)

    df = indicators.add_all(market.fetch_ohlcv(asset, tf))
    warnings = []
    if len(df) < 210:
        warnings.append(f"Λίγα δεδομένα ({len(df)} κεριά): ο EMA200 και η ανάλυση είναι λιγότερο αξιόπιστα")

    htf_name = market.HIGHER_TF[tf]
    htf = None
    if htf_name != tf:
        try:
            htf = indicators.add_all(market.fetch_ohlcv(asset, htf_name))
        except Exception:
            warnings.append(f"Δεν φορτώθηκε το timeframe {htf_name}")

    lv = levels.find_levels(df)
    items = news.get_news(asset) if with_news else []
    fg = news.fear_greed() if with_news and asset.kind == "crypto" else None

    factors = _trend_factors(df, htf, htf_name) + _sentiment_factors(asset, items, fg)
    score, bias = combine(factors, cfg)
    bias, filtered = trend_filter(bias, df.iloc[-1], cfg)
    if filtered:
        warnings.append(filtered)
    plan = _build_plan(bias, df, lv, cfg) if bias != "NEUTRAL" else None

    if with_news and not items:
        warnings.append("Δεν βρέθηκαν πρόσφατες ειδήσεις")

    evs, mk = [], []
    if with_news:
        try:
            evs = events.upcoming(asset)
        except Exception:
            warnings.append("Δεν φορτώθηκε το οικονομικό ημερολόγιο")
        mk = news.market_news()

    return Analysis(asset, tf, float(df["close"].iloc[-1]), df, lv, factors, score, bias, plan, items, fg, warnings,
                    evs, mk)
