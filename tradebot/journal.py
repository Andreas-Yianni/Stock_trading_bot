"""Ημερολόγιο σημάτων (paper trading): κρατάει κάθε πρόταση και ελέγχει αργότερα αν πέτυχε."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import indicators, market
from .config import DATA_DIR
from .engine import Analysis

DB = DATA_DIR / "journal.db"


@contextmanager
def _conn():
    """`with _conn() as c:` κάνει commit (rollback σε λάθος) και κλείνει τη σύνδεση στο τέλος."""
    c = _open()
    try:
        with c:
            yield c
    finally:
        c.close()


def _open():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, symbol TEXT, timeframe TEXT, side TEXT, score REAL,
            entry REAL, stop_loss REAL, tp1 REAL, tp2 REAL,
            status TEXT DEFAULT 'OPEN',  -- OPEN, TP1, TP2, TRAIL, STOP, SL, EXPIRED, SKIP
            closed_at TEXT, result_r REAL
        )""")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(signals)")}
    if "trail" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN trail REAL")  # απόσταση trailing stop (αν υπάρχει)
    if "trend_atr" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN trend_atr REAL")  # έξοδος «τάσης» (πόσα ATR), αν ισχύει
    if "kind" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN kind TEXT")  # stock/crypto (κάποια σύμβολα είναι και τα δύο, π.χ. STX)
    return c


def _asset(s) -> market.Asset:
    return market.resolve(f"{s['symbol']}.US" if s["kind"] == "stock" else s["symbol"])


def record(a: Analysis) -> int | None:
    """Καταγράφει το σήμα, εκτός αν υπάρχει ήδη ανοιχτό για το ίδιο σύμβολο/πλευρά."""
    if not a.plan:
        return None
    p = a.plan
    with _conn() as c:
        dup = c.execute("SELECT id FROM signals WHERE symbol=? AND side=? AND status='OPEN'",
                        (a.asset.symbol, p.side)).fetchone()
        if dup:
            return None
        cur = c.execute(
            "INSERT INTO signals (created_at, symbol, timeframe, side, score, entry, stop_loss, tp1, tp2, trail, "
            "trend_atr, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), a.asset.symbol, a.timeframe, p.side, a.score,
             p.entry, p.stop_loss, p.tp1, p.tp2, p.trail, p.trend_atr, a.asset.kind))
        return cur.lastrowid


def open_signal_id(symbol: str, side: str) -> int | None:
    """Το ανοιχτό σήμα για το σύμβολο/πλευρά (το record δεν γράφει δεύτερο)."""
    with _conn() as c:
        r = c.execute("SELECT id FROM signals WHERE symbol=? AND side=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
                      (symbol, side)).fetchone()
        return r["id"] if r else None


def get(signal_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        return dict(r) if r else None


def trend_stop(side: str, stop: float, extreme: float, atr: float, trend_atr: float) -> float:
    """Νέο stop της εξόδου «τάσης»: (ακραία τιμή από την είσοδο − trend_atr × ATR), ποτέ προς τα πίσω."""
    if side == "LONG":
        return max(stop, extreme - trend_atr * atr)
    return min(stop, extreme + trend_atr * atr)


def _simulate_trend(side, entry, sl, candles, trend_atr, close_at_end, risk):
    sign = 1 if side == "LONG" else -1
    stop, extreme = sl, None
    for row in candles.itertuples():
        adverse = row.low if sign > 0 else row.high
        if sign * (adverse - stop) <= 0:
            # Αν άνοιξε ήδη πέρα από το stop (gap), η εντολή εκτελείται στο άνοιγμα
            px = row.open if sign * (row.open - stop) < 0 else stop
            return ("STOP" if sign * (stop - sl) > 0 else "SL"), sign * (px - entry) / risk, row.Index, px
        favorable = row.high if sign > 0 else row.low
        extreme = favorable if extreme is None else max(extreme, favorable) if sign > 0 else min(extreme, favorable)
        stop = trend_stop(side, stop, extreme, row.atr, trend_atr)
    if close_at_end and len(candles):
        last = float(candles["close"].iloc[-1])
        return "EXPIRED", sign * (last - entry) / risk, candles.index[-1], last
    return None, None, None, None


def simulate(side: str, entry: float, sl: float, tp1: float, tp2: float, candles,
             trail: float | None = None, close_at_end: bool = False, trend_atr: float | None = None,
             risk: float | None = None) -> tuple:
    """Περνάει τα κεριά μετά την είσοδο και επιστρέφει (status, result_r, when, exit_price).
    Κανόνες: μισή θέση κλείνει στο TP1 και το SL πάει στην είσοδο. Το υπόλοιπο κλείνει στο TP2,
    ή, αν δοθεί `trail` (απόσταση σε τιμή), με trailing stop που ακολουθεί την τιμή χωρίς σταθερό στόχο.
    Όταν στο ίδιο κερί χτυπούν stop και στόχος, υποθέτουμε το χειρότερο.
    Με `trend_atr` (έξοδος «τάσης»): όλη η θέση, χωρίς στόχους. Το stop ξεκινά από το SL και στο κλείσιμο
    κάθε κεριού ανεβαίνει στο (υψηλότερο high από την είσοδο − trend_atr × ATR), ποτέ προς τα κάτω
    (τα κεριά θέλουν στήλη atr). Αν η τιμή ανοίξει κάτω από το stop (gap), η έξοδος γίνεται στο άνοιγμα.
    `risk`: το 1R σε τιμή, αν διαφέρει από |entry − sl| (π.χ. είσοδος στο άνοιγμα, ποσότητα από το plan).
    Αν δεν έκλεισε: (None, None, None, None), ή με close_at_end κλείνει στο τελευταίο κερί (EXPIRED).
    Το χρησιμοποιεί και το backtest."""
    if trend_atr is not None:
        return _simulate_trend(side, entry, sl, candles, trend_atr, close_at_end, risk or abs(entry - sl))
    if risk is not None:
        status, r, when, px = simulate(side, entry, sl, tp1, tp2, candles, trail, close_at_end)
        return status, (r * abs(entry - sl) / risk if r is not None else None), when, px
    sign = 1 if side == "LONG" else -1
    risk = abs(entry - sl)
    r1 = abs(tp1 - entry) / risk
    hit_tp1, stop, extreme = False, sl, None
    for row in candles.itertuples():
        adverse, favorable = (row.low, row.high) if sign > 0 else (row.high, row.low)
        if not hit_tp1:
            if sign * (adverse - sl) <= 0:
                return "SL", -1.0, row.Index, sl
            if trail is None and sign * (favorable - tp2) >= 0:
                return "TP2", 0.5 * r1 + 0.5 * abs(tp2 - entry) / risk, row.Index, tp2
            if sign * (favorable - tp1) >= 0:
                hit_tp1, stop, extreme = True, entry, favorable
                if trail is not None:
                    stop = max(entry, extreme - trail) if sign > 0 else min(entry, extreme + trail)
            continue
        # Μετά το TP1: το υπόλοιπο μισό
        if sign * (adverse - stop) <= 0:
            return ("TRAIL" if trail is not None else "TP1"), 0.5 * r1 + 0.5 * sign * (stop - entry) / risk, \
                row.Index, stop
        if trail is None:
            if sign * (favorable - tp2) >= 0:
                return "TP2", 0.5 * r1 + 0.5 * abs(tp2 - entry) / risk, row.Index, tp2
        else:
            extreme = max(extreme, favorable) if sign > 0 else min(extreme, favorable)
            stop = max(stop, extreme - trail) if sign > 0 else min(stop, extreme + trail)
    if close_at_end and len(candles):
        last = float(candles["close"].iloc[-1])
        rest = sign * (last - entry) / risk
        return "EXPIRED", (0.5 * r1 + 0.5 * rest) if hit_tp1 else rest, candles.index[-1], last
    return None, None, None, None


def evaluate_open(max_days: int = 30) -> int:
    """Ελέγχει τα ανοιχτά σήματα με βάση το ιστορικό τιμών. Επιστρέφει πόσα έκλεισαν."""
    closed = 0
    with _conn() as c:
        for s in c.execute("SELECT * FROM signals WHERE status='OPEN'").fetchall():
            created = datetime.fromisoformat(s["created_at"])
            if s["trend_atr"]:
                done = _evaluate_trend(s, created)
                if done:
                    c.execute("UPDATE signals SET status=?, closed_at=?, result_r=? WHERE id=?", (*done, s["id"]))
                    closed += 1
                continue
            try:
                df = market.fetch_ohlcv(_asset(s), "1h")
            except Exception:
                continue
            df = df[df.index > created]
            expired = (datetime.now(timezone.utc) - created).days > max_days
            status, result, when, _ = simulate(s["side"], s["entry"], s["stop_loss"], s["tp1"], s["tp2"], df,
                                               trail=s["trail"], close_at_end=expired)
            if status:
                c.execute("UPDATE signals SET status=?, closed_at=?, result_r=? WHERE id=?",
                          (status, str(when), result, s["id"]))
                closed += 1
    return closed


def replay(s: dict, df=None) -> dict | None:
    """Ένα σήμα με έξοδο «τάσης» όπως θα το έπαιζε το backtest: είσοδος στο άνοιγμα του επόμενου κεριού,
    stop που ανεβαίνει στο κλείσιμο κάθε κεριού. Επιστρέφει {fill, fill_at, status (None = ανοιχτό; SKIP = άνοιξε
    κάτω από το stop), exit, exit_at, r (σε R του plan, χωρίς προμήθειες), stop_now}, ή None χωρίς δεδομένα."""
    tf = s["timeframe"]
    if df is None:
        # Πραγματικές τιμές: η είσοδος και το stop του σήματος είναι αποθηκευμένα σε πραγματικές τιμές
        df = indicators.add_all(market.fetch_ohlcv(_asset(s), tf, adjusted=False))
    df = df[df.index + market.TF_DELTA[tf] > datetime.fromisoformat(s["created_at"])]   # κεριά μετά το σήμα
    if not len(df):
        return None
    sign = 1 if s["side"] == "LONG" else -1
    fill = float(df["open"].iloc[0])
    out = {"fill": fill, "fill_at": df.index[0], "status": None, "exit": None, "exit_at": None, "r": None,
           "stop_now": s["stop_loss"]}
    if sign * (fill - s["stop_loss"]) <= 0:
        return {**out, "status": "SKIP", "exit": fill, "exit_at": df.index[0], "r": 0.0}
    status, result, when, px = simulate(s["side"], fill, s["stop_loss"], None, None, df,
                                        trend_atr=s["trend_atr"], risk=abs(s["entry"] - s["stop_loss"]))
    if status:
        return {**out, "status": status, "exit": px, "exit_at": when, "r": result}
    if sign > 0:
        out["stop_now"] = max(s["stop_loss"], float((df.high.cummax() - s["trend_atr"] * df.atr).max()))
    else:
        out["stop_now"] = min(s["stop_loss"], float((df.low.cummin() + s["trend_atr"] * df.atr).min()))
    return out


def _evaluate_trend(s, created) -> tuple | None:
    """(status, closed_at, result_r) για κλειστό σήμα τάσης, ή None αν είναι ακόμα ανοιχτό."""
    try:
        rp = replay(dict(s))
    except Exception:
        return None
    if not rp or not rp["status"]:
        return None
    return rp["status"], str(rp["exit_at"]), rp["r"]


def all_signals() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC").fetchall()]


def stats() -> dict:
    rows = [s for s in all_signals() if s["status"] not in ("OPEN", "SKIP")]  # SKIP: δεν έγινε είσοδος
    open_n = sum(1 for s in all_signals() if s["status"] == "OPEN")
    if not rows:
        return {"closed": 0, "open": open_n}
    wins = [s for s in rows if (s["result_r"] or 0) > 0]
    total_r = sum(s["result_r"] or 0 for s in rows)
    return {
        "closed": len(rows),
        "open": open_n,
        "win_rate": 100 * len(wins) / len(rows),
        "total_r": total_r,
        "avg_r": total_r / len(rows),
    }
