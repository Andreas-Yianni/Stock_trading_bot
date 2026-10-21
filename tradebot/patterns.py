"""Αναγνώριση patterns (κεριά, δομή αγοράς, breakouts, divergences...) με pandas.

Κάθε pattern είναι μια στήλη με τιμές +1 (bullish), -1 (bearish) ή 0, ΧΩΡΙΣ lookahead: η τιμή
στο κερί t χρησιμοποιεί μόνο δεδομένα μέχρι και το t. Τα swing highs/lows (pivots) θεωρούνται
γνωστά μόνο PIVOT_K κεριά μετά, όταν ένας άνθρωπος θα μπορούσε πράγματι να τα δει.
Θέλει τις στήλες του indicators.add_all().
"""
import numpy as np
import pandas as pd

PIVOT_K = 3

# Ονόματα και επεξήγηση στα ελληνικά (για αναφορές)
NAMES = {
    "pat_structure": ("Δομή αγοράς", "υψηλότερες κορυφές και πάτοι", "χαμηλότερες κορυφές και πάτοι"),
    "pat_breakout": ("Breakout", "η τιμή έσπασε προς τα πάνω την τελευταία κορυφή με όγκο",
                     "η τιμή έσπασε προς τα κάτω τον τελευταίο πάτο με όγκο"),
    "pat_engulfing": ("Engulfing", "πράσινο κερί «κατάπιε» το προηγούμενο κόκκινο",
                      "κόκκινο κερί «κατάπιε» το προηγούμενο πράσινο"),
    "pat_pin": ("Pin bar", "hammer: μακριά κάτω σκιά, οι αγοραστές απέρριψαν τις χαμηλές τιμές",
                "shooting star: μακριά πάνω σκιά, οι πωλητές απέρριψαν τις υψηλές τιμές"),
    "pat_pin_level": ("Pin bar σε επίπεδο", "hammer πάνω σε στήριξη", "shooting star κάτω από αντίσταση"),
    "pat_divergence": ("RSI divergence", "η τιμή έκανε χαμηλότερο πάτο αλλά το RSI υψηλότερο",
                       "η τιμή έκανε υψηλότερη κορυφή αλλά το RSI χαμηλότερη"),
    "pat_squeeze": ("Bollinger squeeze", "η τιμή βγήκε προς τα πάνω μετά από περίοδο ηρεμίας",
                    "η τιμή βγήκε προς τα κάτω μετά από περίοδο ηρεμίας"),
    "pat_double": ("Double bottom/top", "double bottom: δύο πάτοι στο ίδιο επίπεδο και σπάσιμο προς τα πάνω",
                   "double top: δύο κορυφές στο ίδιο επίπεδο και σπάσιμο προς τα κάτω"),
    "pat_pullback": ("Pullback σε τάση", "διόρθωση στον EMA20 μέσα σε ανοδική τάση και αναπήδηση",
                     "αναπήδηση στον EMA20 μέσα σε καθοδική τάση και απόρριψη"),
}


def _confirmed_pivots(s: pd.Series, kind: str, k: int) -> tuple[pd.Series, pd.Series]:
    """(τιμή pivot, θέση pivot) τοποθετημένα στο κερί όπου επιβεβαιώνονται (k κεριά αργότερα)."""
    win = s.rolling(2 * k + 1, center=True)
    is_p = s == (win.max() if kind == "high" else win.min())
    pos = pd.Series(np.arange(len(s)), index=s.index)
    return s.where(is_p).shift(k), pos.where(is_p).shift(k)


def _last_two(known: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Τελευταία και προτελευταία επιβεβαιωμένη τιμή, σε κάθε κερί."""
    events = known.dropna()
    return known.ffill(), events.shift(1).reindex(known.index).ffill()


def add_all(df: pd.DataFrame, k: int = PIVOT_K) -> pd.DataFrame:
    df = df.copy()
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    atr = df["atr"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)

    ph, ph_pos = _confirmed_pivots(h, "high", k)
    pl, pl_pos = _confirmed_pivots(l, "low", k)
    last_ph, prev_ph = _last_two(ph)
    last_pl, prev_pl = _last_two(pl)
    last_ph_pos, prev_ph_pos = _last_two(ph_pos)
    last_pl_pos, prev_pl_pos = _last_two(pl_pos)

    # Δομή αγοράς: HH + HL = ανοδική, LH + LL = καθοδική (κατάσταση, όχι γεγονός)
    up = (last_ph > prev_ph) & (last_pl > prev_pl)
    down = (last_ph < prev_ph) & (last_pl < prev_pl)
    df["pat_structure"] = np.where(up, 1, np.where(down, -1, 0))

    # Breakout: κλείσιμο πάνω από την τελευταία κορυφή (κάτω από τον τελευταίο πάτο) με αυξημένο όγκο
    vol_ok = df["volume"] > df["vol_avg20"]
    br_up = (c > last_ph) & (c.shift() <= last_ph.shift()) & vol_ok
    br_dn = (c < last_pl) & (c.shift() >= last_pl.shift()) & vol_ok
    df["pat_breakout"] = np.where(br_up, 1, np.where(br_dn, -1, 0))

    # Engulfing
    po, pc = o.shift(), c.shift()
    bull_e = (pc < po) & (c > o) & (o <= pc) & (c >= po) & (body > (pc - po).abs())
    bear_e = (pc > po) & (c < o) & (o >= pc) & (c <= po) & (body > (pc - po).abs())
    df["pat_engulfing"] = np.where(bull_e, 1, np.where(bear_e, -1, 0))

    # Pin bar (hammer / shooting star)
    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    hammer = (lower >= 2 * body) & (lower >= 0.6 * rng) & ((c - l) / rng >= 0.66)
    star = (upper >= 2 * body) & (upper >= 0.6 * rng) & ((h - c) / rng >= 0.66)
    df["pat_pin"] = np.where(hammer, 1, np.where(star, -1, 0))
    at_sup = (l - last_pl).abs() <= 0.5 * atr
    at_res = (h - last_ph).abs() <= 0.5 * atr
    df["pat_pin_level"] = np.where(hammer & at_sup, 1, np.where(star & at_res, -1, 0))

    # RSI divergence, τη στιγμή που επιβεβαιώνεται ο νέος πάτος / η νέα κορυφή
    rsi = df["rsi"]
    rsi_at_pl = rsi.shift(k).where(pl.notna())
    rsi_at_ph = rsi.shift(k).where(ph.notna())
    last_rpl, prev_rpl = _last_two(rsi_at_pl)
    last_rph, prev_rph = _last_two(rsi_at_ph)
    bull_d = pl.notna() & (last_pl < prev_pl) & (last_rpl > prev_rpl) & (prev_rpl < 40)
    bear_d = ph.notna() & (last_ph > prev_ph) & (last_rph < prev_rph) & (prev_rph > 60)
    df["pat_divergence"] = np.where(bull_d, 1, np.where(bear_d, -1, 0))

    # Bollinger squeeze: το εύρος των bands ήταν στο χαμηλότερο 120 κεριών μέσα στα τελευταία 5,
    # και τώρα η τιμή κλείνει έξω από τα bands
    bw = (df["bb_up"] - df["bb_low"]) / df["bb_mid"]
    squeezed = (bw <= bw.rolling(120).min() * 1.05).rolling(5).max().astype(bool)
    df["pat_squeeze"] = np.where(squeezed & (c > df["bb_up"]), 1,
                                 np.where(squeezed & (c < df["bb_low"]), -1, 0))

    # Double bottom / top: δύο πάτοι (κορυφές) σε απόσταση ≤0.5 ATR, και κλείσιμο πέρα από
    # την κορυφή (τον πάτο) που βρίσκεται ανάμεσά τους (neckline)
    dbl_b = ((last_pl - prev_pl).abs() <= 0.5 * atr) & (last_ph_pos > prev_pl_pos) & (last_ph_pos < last_pl_pos)
    dbl_t = ((last_ph - prev_ph).abs() <= 0.5 * atr) & (last_pl_pos > prev_ph_pos) & (last_pl_pos < last_ph_pos)
    df["pat_double"] = np.where(dbl_b & (c > last_ph) & (c.shift() <= last_ph.shift()), 1,
                                np.where(dbl_t & (c < last_pl) & (c.shift() >= last_pl.shift()), -1, 0))

    # Pullback μέσα σε τάση: άγγιξε τον EMA20 και έκλεισε ξανά προς την κατεύθυνση της τάσης
    e20, e50, e200 = df["ema20"], df["ema50"], df["ema200"]
    uptrend = (e20 > e50) & (e50 > e200) & (c > e200)
    downtrend = (e20 < e50) & (e50 < e200) & (c < e200)
    pb_up = uptrend & (l <= e20) & (c > e20) & (c > o)
    pb_dn = downtrend & (h >= e20) & (c < e20) & (c < o)
    df["pat_pullback"] = np.where(pb_up, 1, np.where(pb_dn, -1, 0))
    return df
